from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities import (
    Application,
    ApplicationForm,
    Decision,
    InfoRequest,
    OfferOption,
)
from app.domain.enums import (
    ApplicationStatus,
    CollateralType,
    CompanyKind,
    DecisionOutcome,
    Purpose,
    Variant,
)
from app.domain.errors import InvalidStatus, NotFound, OfferExpired, ValidationFailed
from app.domain.products import ProductCatalog
from app.domain.value_objects import Collateral, Money

S = ApplicationStatus
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TTL = timedelta(days=7)
CLIENT = uuid4()

UNSECURED_FORM = ApplicationForm(
    purpose=Purpose.DEVELOPMENT,
    product_code="LUMP_SUM",
    variant=Variant.UNSECURED,
    amount=Money.of("2000000"),
    term_months=12,
    owner_guarantee=True,
)


def _collateral(value: str) -> Collateral:
    return Collateral(
        type=CollateralType.REAL_ESTATE,
        description="Производственное помещение",
        estimated_value=Money.of(value),
    )


def _secured_form(amount: str, collateral: Collateral | None) -> ApplicationForm:
    return ApplicationForm(
        purpose=Purpose.REGULAR_PURCHASES,
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        amount=Money.of(amount),
        term_months=60,
        owner_guarantee=False,
        collateral=collateral,
    )


def _draft(
    catalog: ProductCatalog,
    form: ApplicationForm = UNSECURED_FORM,
    kind: CompanyKind = CompanyKind.LLC,
) -> Application:
    return Application.create_draft(
        id=uuid4(),
        applicant_id=CLIENT,
        inn="7701234567",
        company_kind=kind,
        form=form,
        product=catalog.get(form.product_code),
        now=NOW,
    )


def _in_status(catalog: ProductCatalog, status: ApplicationStatus) -> Application:
    app = _draft(catalog)
    return Application(
        id=app.id,
        applicant_id=app.applicant_id,
        inn=app.inn,
        company_kind=app.company_kind,
        purpose=app.purpose,
        product_code=app.product_code,
        variant=app.variant,
        amount=app.amount,
        term_months=app.term_months,
        owner_guarantee=app.owner_guarantee,
        status=status,
        created_at=NOW,
    )


def _counter_offer(catalog: ProductCatalog) -> tuple[Application, OfferOption, OfferOption]:
    unsecured = OfferOption(
        id=uuid4(),
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        amount=Money.of("3600000"),
        term_months=24,
        rate=None,
    )
    secured = replace(unsecured, id=uuid4(), variant=Variant.SECURED, amount=Money.of("4500000"))
    app = _in_status(catalog, S.SCORING)
    app.decisions.append(
        Decision(
            id=uuid4(),
            rule_code="REVENUE_LIMIT_COUNTER_OFFER",
            outcome=DecisionOutcome.COUNTER_OFFER,
            rate=None,
            reasons=("Сумма выше лимита от выручки без залога",),
            offers=(unsecured, secured),
            decided_by=None,
            comment=None,
            decided_at=NOW,
        )
    )
    app.transition_to(S.COUNTER_OFFER, actor_id=None, reason="", now=NOW, offer_ttl=TTL)
    return app, unsecured, secured


class TestCreateDraft:
    def test_new_application_is_draft_without_history(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog)

        assert app.status is S.DRAFT
        assert app.history == []
        assert app.expires_at is None
        assert app.created_at == NOW

    def test_collateral_exactly_half_of_amount_is_enough(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog, _secured_form("12000000", _collateral("6000000")))

        assert app.collateral is not None

    def test_collateral_below_half_of_amount_is_rejected(self, catalog: ProductCatalog) -> None:
        with pytest.raises(ValidationFailed) as exc:
            _draft(catalog, _secured_form("12000000", _collateral("5999999.99")))

        assert [d.code for d in exc.value.details] == ["COLLATERAL_COVERAGE"]

    def test_collateral_is_not_accepted_for_unsecured_variant(
        self, catalog: ProductCatalog
    ) -> None:
        form = replace(UNSECURED_FORM, collateral=_collateral("5000000"))

        with pytest.raises(ValidationFailed) as exc:
            _draft(catalog, form)

        assert [d.code for d in exc.value.details] == ["COLLATERAL_NOT_ALLOWED"]

    def test_secured_draft_may_be_saved_before_collateral_is_filled(
        self, catalog: ProductCatalog
    ) -> None:
        app = _draft(catalog, _secured_form("12000000", None))

        assert app.collateral is None

    def test_form_for_another_product_is_rejected(self, catalog: ProductCatalog) -> None:
        with pytest.raises(ValueError, match="REVOLVING_LINE"):
            Application.create_draft(
                id=uuid4(),
                applicant_id=CLIENT,
                inn="7701234567",
                company_kind=CompanyKind.LLC,
                form=UNSECURED_FORM,
                product=catalog.get("REVOLVING_LINE"),
                now=NOW,
            )


class TestEditing:
    @pytest.mark.parametrize("status", [S.DRAFT, S.NEEDS_INFO])
    def test_form_is_editable_in_draft_and_needs_info(
        self, catalog: ProductCatalog, status: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, status)
        later = NOW + timedelta(hours=1)

        app.update_form(
            replace(UNSECURED_FORM, amount=Money.of("1500000")),
            catalog.get("LUMP_SUM"),
            now=later,
        )

        assert app.amount == Money.of("1500000")
        assert app.updated_at == later

    @pytest.mark.parametrize("status", [s for s in S if s not in {S.DRAFT, S.NEEDS_INFO}])
    def test_form_is_frozen_in_other_statuses(
        self, catalog: ProductCatalog, status: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, status)

        with pytest.raises(InvalidStatus):
            app.update_form(
                replace(UNSECURED_FORM, amount=Money.of("1500000")),
                catalog.get("LUMP_SUM"),
                now=NOW,
            )

        assert app.amount == Money.of("2000000")

    def test_update_checks_collateral_coverage(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog, _secured_form("12000000", None))

        with pytest.raises(ValidationFailed):
            app.update_form(
                _secured_form("12000000", _collateral("1000000")),
                catalog.get("REVOLVING_LINE"),
                now=NOW,
            )

        assert app.collateral is None


class TestSubmit:
    def test_submit_moves_draft_to_submitted(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog)

        app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=NOW)

        assert app.status is S.SUBMITTED

    def test_llc_without_collateral_needs_owner_guarantee(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog, replace(UNSECURED_FORM, owner_guarantee=False))

        with pytest.raises(ValidationFailed) as exc:
            app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=NOW)

        assert [d.code for d in exc.value.details] == ["OWNER_GUARANTEE"]
        assert app.status is S.DRAFT

    def test_sole_proprietor_does_not_need_owner_guarantee(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog, replace(UNSECURED_FORM, owner_guarantee=False), CompanyKind.IE)

        app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=NOW)

        assert app.status is S.SUBMITTED

    def test_llc_with_collateral_does_not_need_owner_guarantee(
        self, catalog: ProductCatalog
    ) -> None:
        app = _draft(catalog, _secured_form("12000000", _collateral("9000000")))

        app.submit(catalog.get("REVOLVING_LINE"), actor_id=CLIENT, now=NOW)

        assert app.status is S.SUBMITTED

    def test_secured_variant_cannot_be_submitted_without_collateral(
        self, catalog: ProductCatalog
    ) -> None:
        app = _draft(catalog, _secured_form("12000000", None))

        with pytest.raises(ValidationFailed) as exc:
            app.submit(catalog.get("REVOLVING_LINE"), actor_id=CLIENT, now=NOW)

        assert [d.code for d in exc.value.details] == ["COLLATERAL_REQUIRED"]

    def test_resubmit_from_needs_info_resolves_info_request(self, catalog: ProductCatalog) -> None:
        app = _in_status(catalog, S.CHECKING)
        app.request_info(
            ["STATEMENT_6_MONTHS"], "Нужна выписка за 6 месяцев", actor_id=None, now=NOW
        )
        later = NOW + timedelta(days=1)

        app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=later)

        assert app.status is S.SUBMITTED
        assert app.info_requests[-1].resolved_at == later
        assert app.open_info_request is None

    @pytest.mark.parametrize("status", [s for s in S if s not in {S.DRAFT, S.NEEDS_INFO}])
    def test_submit_is_only_possible_from_draft_or_needs_info(
        self, catalog: ProductCatalog, status: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, status)

        with pytest.raises(InvalidStatus):
            app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=NOW)


class TestHistoryAndEvents:
    def test_transition_records_history_and_event(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog)

        app.submit(catalog.get("LUMP_SUM"), actor_id=CLIENT, now=NOW)
        app.transition_to(S.CHECKING, actor_id=None, reason="Конвейер запущен", now=NOW)

        assert [(t.from_status, t.to_status) for t in app.history] == [
            (S.DRAFT, S.SUBMITTED),
            (S.SUBMITTED, S.CHECKING),
        ]
        assert app.history[0].actor_id == CLIENT
        assert app.history[1].actor_id is None
        assert app.history[1].reason == "Конвейер запущен"
        assert app.history[1].at == NOW

        events = app.pull_events()
        assert [(e.from_status, e.to_status) for e in events] == [
            (S.DRAFT, S.SUBMITTED),
            (S.SUBMITTED, S.CHECKING),
        ]
        assert all(e.application_id == app.id and e.applicant_id == CLIENT for e in events)
        assert not any(e.is_final for e in events)

    def test_pull_events_empties_the_queue(self, catalog: ProductCatalog) -> None:
        app = _draft(catalog)
        app.cancel(actor_id=CLIENT, reason="Передумал", now=NOW)

        first = app.pull_events()

        assert len(first) == 1
        assert first[0].is_final
        assert app.pull_events() == []


class TestOfferExpiry:
    @pytest.mark.parametrize("target", [S.APPROVED, S.COUNTER_OFFER])
    def test_offer_statuses_set_expires_at(
        self, catalog: ProductCatalog, target: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, S.SCORING)

        app.transition_to(target, actor_id=None, reason="", now=NOW, offer_ttl=TTL)

        assert app.expires_at == NOW + TTL

    @pytest.mark.parametrize("target", [S.APPROVED, S.COUNTER_OFFER])
    def test_offer_status_without_ttl_is_a_programming_error(
        self, catalog: ProductCatalog, target: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, S.SCORING)

        with pytest.raises(ValueError, match="offer_ttl"):
            app.transition_to(target, actor_id=None, reason="", now=NOW)

        assert app.status is S.SCORING

    @pytest.mark.parametrize("target", [S.MANUAL_REVIEW, S.REJECTED])
    def test_other_statuses_do_not_set_expires_at(
        self, catalog: ProductCatalog, target: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, S.SCORING)

        app.transition_to(target, actor_id=None, reason="", now=NOW, offer_ttl=TTL)

        assert app.expires_at is None

    def test_leaving_offer_status_clears_expires_at(self, catalog: ProductCatalog) -> None:
        app = _in_status(catalog, S.SCORING)
        app.transition_to(S.APPROVED, actor_id=None, reason="", now=NOW, offer_ttl=TTL)

        app.cancel(actor_id=None, reason="Истёк срок", now=NOW + TTL)

        assert app.expires_at is None


class TestAcceptOffer:
    def test_accept_approved_moves_to_signing(self, catalog: ProductCatalog) -> None:
        app = _in_status(catalog, S.SCORING)
        app.transition_to(S.APPROVED, actor_id=None, reason="", now=NOW, offer_ttl=TTL)

        app.accept_offer(None, actor_id=CLIENT, now=NOW + TTL - timedelta(seconds=1))

        assert app.status is S.SIGNING

    def test_accept_after_expiry_raises_offer_expired(self, catalog: ProductCatalog) -> None:
        app = _in_status(catalog, S.SCORING)
        app.transition_to(S.APPROVED, actor_id=None, reason="", now=NOW, offer_ttl=TTL)

        with pytest.raises(OfferExpired):
            app.accept_offer(None, actor_id=CLIENT, now=NOW + TTL)

        assert app.status is S.APPROVED

    def test_unsecured_counter_offer_goes_to_signing_with_offer_terms(
        self, catalog: ProductCatalog
    ) -> None:
        app, unsecured, _ = _counter_offer(catalog)

        app.accept_offer(unsecured.id, actor_id=CLIENT, now=NOW)

        assert app.status is S.SIGNING
        assert app.amount == Money.of("3600000")
        assert app.term_months == 24
        assert app.variant is Variant.UNSECURED
        assert app.accepted_offer_id == unsecured.id

    def test_secured_counter_offer_asks_for_collateral(self, catalog: ProductCatalog) -> None:
        app, _, secured = _counter_offer(catalog)

        app.accept_offer(secured.id, actor_id=CLIENT, now=NOW)

        assert app.status is S.NEEDS_INFO
        assert app.variant is Variant.SECURED
        assert app.amount == Money.of("4500000")
        request = app.open_info_request
        assert isinstance(request, InfoRequest)
        assert request.items == ("COLLATERAL",)

    def test_counter_offer_requires_offer_id(self, catalog: ProductCatalog) -> None:
        app, _, _ = _counter_offer(catalog)

        with pytest.raises(ValidationFailed):
            app.accept_offer(None, actor_id=CLIENT, now=NOW)

    def test_unknown_offer_id_is_not_found(self, catalog: ProductCatalog) -> None:
        app, _, _ = _counter_offer(catalog)

        with pytest.raises(NotFound):
            app.accept_offer(uuid4(), actor_id=CLIENT, now=NOW)

        assert app.status is S.COUNTER_OFFER

    @pytest.mark.parametrize("status", [s for s in S if s not in {S.APPROVED, S.COUNTER_OFFER}])
    def test_accept_outside_offer_statuses_is_invalid(
        self, catalog: ProductCatalog, status: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, status)

        with pytest.raises(InvalidStatus):
            app.accept_offer(None, actor_id=CLIENT, now=NOW)


class TestRequestInfo:
    @pytest.mark.parametrize("status", [S.CHECKING, S.MANUAL_REVIEW])
    def test_request_info_opens_request_and_moves_to_needs_info(
        self, catalog: ProductCatalog, status: ApplicationStatus
    ) -> None:
        app = _in_status(catalog, status)
        analyst = uuid4()

        app.request_info(["COLLATERAL_DESCRIPTION"], "Опишите залог", actor_id=analyst, now=NOW)

        assert app.status is S.NEEDS_INFO
        request = app.open_info_request
        assert request is not None
        assert request.items == ("COLLATERAL_DESCRIPTION",)
        assert request.created_by == analyst
        assert request.resolved_at is None

    def test_request_info_needs_at_least_one_item(self, catalog: ProductCatalog) -> None:
        app = _in_status(catalog, S.MANUAL_REVIEW)

        with pytest.raises(ValidationFailed):
            app.request_info([], "", actor_id=None, now=NOW)

        assert app.status is S.MANUAL_REVIEW
