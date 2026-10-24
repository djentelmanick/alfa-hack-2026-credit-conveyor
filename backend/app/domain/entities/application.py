from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Self
from uuid import UUID

from app.domain.entities.parts import (
    ApplicationForm,
    BankStatement,
    CheckResult,
    Consent,
    Decision,
    InfoRequest,
    SourceSnapshot,
    StatusTransition,
)
from app.domain.enums import ApplicationStatus, CompanyKind, Purpose, Variant
from app.domain.errors import FieldError, InvalidStatus, NotFound, OfferExpired, ValidationFailed
from app.domain.events import StatusChangedEvent
from app.domain.scoring.result import ScoringResult
from app.domain.state_machine import (
    EDITABLE_STATUSES,
    OFFER_STATUSES,
    ApplicationStateMachine,
)
from app.domain.value_objects import Collateral, Money

if TYPE_CHECKING:
    # Продукты сами импортируют сущности договоров, поэтому прямой импорт дал бы цикл.
    from app.domain.products.loan_product import LoanProduct

S = ApplicationStatus


class Application:
    """Агрегат заявки. Статус меняется только через transition_to и методы-сценарии."""

    def __init__(
        self,
        *,
        id: UUID,
        applicant_id: UUID,
        inn: str,
        company_kind: CompanyKind,
        purpose: Purpose,
        product_code: str,
        variant: Variant,
        amount: Money,
        term_months: int,
        owner_guarantee: bool = False,
        collateral: Collateral | None = None,
        status: ApplicationStatus = S.DRAFT,
        created_at: datetime,
        updated_at: datetime | None = None,
        expires_at: datetime | None = None,
        accepted_offer_id: UUID | None = None,
        contract_id: UUID | None = None,
        consents: Iterable[Consent] = (),
        statements: Iterable[BankStatement] = (),
        snapshots: Iterable[SourceSnapshot] = (),
        checks: Iterable[CheckResult] = (),
        scoring: ScoringResult | None = None,
        decisions: Iterable[Decision] = (),
        info_requests: Iterable[InfoRequest] = (),
        history: Iterable[StatusTransition] = (),
    ) -> None:
        self.id = id
        self.applicant_id = applicant_id
        self.inn = inn
        self.company_kind = company_kind
        self.purpose = purpose
        self.product_code = product_code
        self.variant = variant
        self.amount = amount
        self.term_months = term_months
        self.owner_guarantee = owner_guarantee
        self.collateral = collateral
        self._status = status
        self.created_at = created_at
        self.updated_at = updated_at or created_at
        self.expires_at = expires_at
        self.accepted_offer_id = accepted_offer_id
        self.contract_id = contract_id
        self.consents = list(consents)
        self.statements = list(statements)
        self.snapshots = list(snapshots)
        self.checks = list(checks)
        self.scoring = scoring
        self.decisions = list(decisions)
        self.info_requests = list(info_requests)
        self.history = list(history)
        self._events: list[StatusChangedEvent] = []

    @classmethod
    def create_draft(
        cls,
        *,
        id: UUID,
        applicant_id: UUID,
        inn: str,
        company_kind: CompanyKind,
        form: ApplicationForm,
        product: LoanProduct,
        now: datetime,
    ) -> Self:
        _ensure_collateral_fits(form, product)
        return cls(
            id=id,
            applicant_id=applicant_id,
            inn=inn,
            company_kind=company_kind,
            purpose=form.purpose,
            product_code=form.product_code,
            variant=form.variant,
            amount=form.amount,
            term_months=form.term_months,
            owner_guarantee=form.owner_guarantee,
            collateral=form.collateral,
            created_at=now,
        )

    @property
    def status(self) -> ApplicationStatus:
        return self._status

    @property
    def form(self) -> ApplicationForm:
        return ApplicationForm(
            purpose=self.purpose,
            product_code=self.product_code,
            variant=self.variant,
            amount=self.amount,
            term_months=self.term_months,
            owner_guarantee=self.owner_guarantee,
            collateral=self.collateral,
        )

    @property
    def current_decision(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def open_info_request(self) -> InfoRequest | None:
        last = self.info_requests[-1] if self.info_requests else None
        return last if last is not None and last.resolved_at is None else None

    def is_active(self) -> bool:
        return ApplicationStateMachine.is_active(self._status)

    def update_form(self, form: ApplicationForm, product: LoanProduct, *, now: datetime) -> None:
        self._ensure_editable()
        _ensure_collateral_fits(form, product)
        self._apply_form(form)
        self.updated_at = now

    def submit(self, product: LoanProduct, *, actor_id: UUID, now: datetime) -> None:
        self._ensure_editable()
        _ensure_ready_to_submit(self.form, product, self.company_kind)
        if (request := self.open_info_request) is not None:
            request.resolved_at = now
        self.transition_to(S.SUBMITTED, actor_id=actor_id, reason="Заявка отправлена", now=now)

    def request_info(
        self, items: Sequence[str], message: str, *, actor_id: UUID | None, now: datetime
    ) -> None:
        if not items:
            raise ValidationFailed(
                [FieldError("items", "REQUIRED", "Укажите, каких данных не хватает")]
            )
        self.transition_to(S.NEEDS_INFO, actor_id=actor_id, reason=message, now=now)
        self.info_requests.append(
            InfoRequest(items=tuple(items), message=message, created_by=actor_id, created_at=now)
        )

    def accept_offer(self, option_id: UUID | None, *, actor_id: UUID, now: datetime) -> None:
        if self._status not in OFFER_STATUSES:
            raise InvalidStatus("Принять можно только одобрение или контрпредложение")
        if self.expires_at is not None and now >= self.expires_at:
            raise OfferExpired()
        if self._status is S.APPROVED:
            self.transition_to(S.SIGNING, actor_id=actor_id, reason="Условия приняты", now=now)
            return

        if option_id is None:
            raise ValidationFailed(
                [FieldError("offer_id", "REQUIRED", "Выберите вариант контрпредложения")]
            )
        decision = self.current_decision
        option = decision.offer(option_id) if decision is not None else None
        if option is None:
            raise NotFound("Вариант контрпредложения не найден")

        self.product_code = option.product_code
        self.variant = option.variant
        self.amount = option.amount
        self.term_months = option.term_months
        self.accepted_offer_id = option.id
        if option.variant is Variant.SECURED:
            self.request_info(
                ["COLLATERAL"],
                "Заполните сведения о залоге",
                actor_id=actor_id,
                now=now,
            )
        else:
            self.collateral = None
            self.transition_to(
                S.SIGNING, actor_id=actor_id, reason="Контрпредложение принято", now=now
            )

    def cancel(self, *, actor_id: UUID | None, reason: str, now: datetime) -> None:
        self.transition_to(S.CANCELLED, actor_id=actor_id, reason=reason, now=now)

    def transition_to(
        self,
        target: ApplicationStatus,
        *,
        actor_id: UUID | None,
        reason: str,
        now: datetime,
        offer_ttl: timedelta | None = None,
    ) -> None:
        source = self._status
        ApplicationStateMachine.transition(source, target)
        if target in OFFER_STATUSES:
            if offer_ttl is None:
                raise ValueError(f"Для перехода в {target} нужен offer_ttl")
            self.expires_at = now + offer_ttl
        else:
            self.expires_at = None

        self._status = target
        self.updated_at = now
        self.history.append(
            StatusTransition(
                from_status=source, to_status=target, actor_id=actor_id, reason=reason, at=now
            )
        )
        self._events.append(
            StatusChangedEvent(
                application_id=self.id,
                applicant_id=self.applicant_id,
                from_status=source,
                to_status=target,
                reason=reason,
                at=now,
            )
        )

    def pull_events(self) -> list[StatusChangedEvent]:
        events, self._events = self._events, []
        return events

    def _ensure_editable(self) -> None:
        if self._status not in EDITABLE_STATUSES:
            raise InvalidStatus("Анкету можно менять только в черновике или при дозапросе")

    def _apply_form(self, form: ApplicationForm) -> None:
        self.purpose = form.purpose
        self.product_code = form.product_code
        self.variant = form.variant
        self.amount = form.amount
        self.term_months = form.term_months
        self.owner_guarantee = form.owner_guarantee
        self.collateral = form.collateral


def _ensure_product_matches(form: ApplicationForm, product: LoanProduct) -> None:
    if form.product_code != product.code:
        raise ValueError(f"Анкета на {form.product_code}, а передан продукт {product.code}")


def _ensure_collateral_fits(form: ApplicationForm, product: LoanProduct) -> None:
    """То, что должно выполняться уже в черновике: залог можно не указать, но неверный нельзя."""
    _ensure_product_matches(form, product)
    if form.collateral is None:
        return
    if form.variant is Variant.UNSECURED:
        raise ValidationFailed(
            [
                FieldError(
                    "collateral",
                    "COLLATERAL_NOT_ALLOWED",
                    "Залог указывается только в варианте с залогом",
                )
            ]
        )
    min_pct = product.variant(form.variant).collateral_coverage_min_pct
    if min_pct is not None and not form.collateral.covers(form.amount, min_pct):
        raise ValidationFailed(
            [
                FieldError(
                    "collateral.estimated_value",
                    "COLLATERAL_COVERAGE",
                    f"Оценка залога должна покрывать не меньше {min_pct}% суммы",
                )
            ]
        )


def _ensure_ready_to_submit(
    form: ApplicationForm, product: LoanProduct, company_kind: CompanyKind
) -> None:
    _ensure_collateral_fits(form, product)
    if form.variant is Variant.SECURED and form.collateral is None:
        raise ValidationFailed([FieldError("collateral", "COLLATERAL_REQUIRED", "Укажите залог")])
    if (
        company_kind is CompanyKind.LLC
        and form.variant is Variant.UNSECURED
        and not form.owner_guarantee
    ):
        raise ValidationFailed(
            [
                FieldError(
                    "owner_guarantee",
                    "OWNER_GUARANTEE",
                    "Для ООО без залога нужно поручительство собственника",
                )
            ]
        )
