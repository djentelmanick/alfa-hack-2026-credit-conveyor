"""Действия аналитика над заявкой на ручной проверке (SCOPE раздел 7, BACKEND_SPEC раздел 5)."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities import Application, OfferOption
from app.domain.entities.application import ANALYST_RULE_CODE
from app.domain.enums import ApplicationStatus, DecisionOutcome, SourceType, Variant
from app.domain.errors import InvalidStatus, SourcesNotReady, ValidationFailed
from app.domain.events import StatusChangedEvent
from app.domain.value_objects import Money
from tests.fakes.analyst import manual_review_application
from tests.fakes.context import make_application
from tests.fakes.scenarios import NOW

S = ApplicationStatus
TTL = timedelta(days=7)
ANALYST = uuid4()
COMMENT = "Залог проверен, оценка подтверждена"

type Action = Callable[[Application, str], None]


def _offer() -> OfferOption:
    return OfferOption(
        id=uuid4(),
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        amount=Money.of(1_500_000),
        term_months=24,
        rate=Decimal("26.5"),
    )


def approve(app: Application, comment: str) -> None:
    app.approve_by_analyst(
        Decimal("23.5"), actor_id=ANALYST, comment=comment, now=NOW, offer_ttl=TTL
    )


def counter_offer(app: Application, comment: str) -> None:
    app.counter_offer_by_analyst(
        [_offer()], actor_id=ANALYST, comment=comment, now=NOW, offer_ttl=TTL
    )


def request_info(app: Application, comment: str) -> None:
    app.request_info_by_analyst(
        {"TAX_CERTIFICATE": "Справка об отсутствии налоговой задолженности"},
        actor_id=ANALYST,
        comment=comment,
        now=NOW,
    )


def reject(app: Application, comment: str) -> None:
    app.reject_by_analyst("TAX_DEBT_OVER_10K", actor_id=ANALYST, comment=comment, now=NOW)


def recheck(app: Application, comment: str) -> None:
    app.start_recheck(actor_id=ANALYST, comment=comment, now=NOW)


ACTIONS: list[tuple[Action, ApplicationStatus]] = [
    (approve, S.APPROVED),
    (counter_offer, S.COUNTER_OFFER),
    (request_info, S.NEEDS_INFO),
    (reject, S.REJECTED),
    (recheck, S.CHECKING),
]
ACTION_IDS = [action.__name__ for action, _ in ACTIONS]


@pytest.mark.parametrize(("action", "target"), ACTIONS, ids=ACTION_IDS)
def test_action_moves_application_out_of_manual_review(
    action: Action, target: ApplicationStatus
) -> None:
    app = manual_review_application()

    action(app, COMMENT)

    assert app.status is target
    last = app.history[-1]
    assert (last.from_status, last.to_status, last.actor_id) == (S.MANUAL_REVIEW, target, ANALYST)
    # Комментарий внутренний: в историю, которую видит клиент, он не попадает.
    assert COMMENT not in last.reason
    (event,) = app.pull_events()
    assert isinstance(event, StatusChangedEvent)
    assert (event.from_status, event.to_status) == (S.MANUAL_REVIEW, target)


@pytest.mark.parametrize(("action", "target"), ACTIONS, ids=ACTION_IDS)
@pytest.mark.parametrize("comment", ["", "   "])
def test_comment_is_required(action: Action, target: ApplicationStatus, comment: str) -> None:
    app = manual_review_application()
    decisions = len(app.decisions)

    with pytest.raises(ValidationFailed) as exc:
        action(app, comment)

    assert [(e.field, e.code) for e in exc.value.details] == [("comment", "REQUIRED")]
    assert app.status is S.MANUAL_REVIEW
    assert len(app.decisions) == decisions


@pytest.mark.parametrize(("action", "target"), ACTIONS, ids=ACTION_IDS)
@pytest.mark.parametrize("status", [S.CHECKING, S.SCORING, S.APPROVED])
def test_actions_are_allowed_only_in_manual_review(
    action: Action, target: ApplicationStatus, status: ApplicationStatus
) -> None:
    app = make_application()
    path = {
        S.CHECKING: [S.SUBMITTED, S.CHECKING],
        S.SCORING: [S.SUBMITTED, S.CHECKING, S.SCORING],
        S.APPROVED: [S.SUBMITTED, S.CHECKING, S.SCORING, S.APPROVED],
    }[status]
    for step in path:
        app.transition_to(step, actor_id=None, reason="тест", now=NOW, offer_ttl=TTL)

    with pytest.raises(InvalidStatus):
        action(app, COMMENT)
    assert app.status is status


def test_approve_records_analyst_decision_and_offer_expiry() -> None:
    app = manual_review_application()

    approve(app, f"  {COMMENT}  ")

    decision = app.current_decision
    assert decision is not None
    assert decision.rule_code == ANALYST_RULE_CODE
    assert decision.outcome is DecisionOutcome.APPROVED
    assert decision.rate == Decimal("23.5")
    assert (decision.decided_by, decision.comment) == (ANALYST, COMMENT)
    assert app.expires_at == NOW + TTL
    # Системное решение остаётся в истории решений: отчёт показывает оба.
    assert app.decisions[0].decided_by is None


def test_approve_with_failed_source_is_sources_not_ready() -> None:
    app = manual_review_application(failed=[SourceType.BKI])

    with pytest.raises(SourcesNotReady):
        approve(app, COMMENT)

    assert app.status is S.MANUAL_REVIEW
    assert app.failed_sources() == [SourceType.BKI]


def test_approve_without_any_source_answers_is_sources_not_ready() -> None:
    app = manual_review_application()
    app.record_snapshots([])

    with pytest.raises(SourcesNotReady):
        approve(app, COMMENT)


@pytest.mark.parametrize("action", [counter_offer, request_info, reject, recheck])
def test_other_actions_do_not_require_all_sources(action: Action) -> None:
    app = manual_review_application(failed=[SourceType.BKI])

    action(app, COMMENT)

    assert app.status is not S.MANUAL_REVIEW


def test_counter_offer_keeps_options_and_rate_of_first() -> None:
    app = manual_review_application()
    first, second = _offer(), _offer()

    app.counter_offer_by_analyst(
        [first, second], actor_id=ANALYST, comment=COMMENT, now=NOW, offer_ttl=TTL
    )

    decision = app.current_decision
    assert decision is not None
    assert decision.outcome is DecisionOutcome.COUNTER_OFFER
    assert decision.offers == (first, second)
    assert decision.rate == first.rate
    assert app.expires_at == NOW + TTL


def test_counter_offer_needs_at_least_one_option() -> None:
    app = manual_review_application()

    with pytest.raises(ValidationFailed) as exc:
        app.counter_offer_by_analyst([], actor_id=ANALYST, comment=COMMENT, now=NOW, offer_ttl=TTL)

    assert exc.value.details[0].field == "options"
    assert app.status is S.MANUAL_REVIEW


def test_secured_counter_offer_accepted_by_client_goes_to_needs_info() -> None:
    app = manual_review_application()
    secured = OfferOption(
        id=uuid4(),
        product_code="LUMP_SUM",
        variant=Variant.SECURED,
        amount=Money.of(4_500_000),
        term_months=24,
        rate=Decimal("24.5"),
    )
    app.counter_offer_by_analyst(
        [secured], actor_id=ANALYST, comment=COMMENT, now=NOW, offer_ttl=TTL
    )

    app.accept_offer(secured.id, actor_id=app.applicant_id, now=NOW)

    assert app.status is S.NEEDS_INFO
    request = app.open_info_request
    assert request is not None
    assert request.items == ("COLLATERAL",)


def test_request_info_keeps_analyst_messages_for_client() -> None:
    app = manual_review_application()

    request_info(app, COMMENT)

    request = app.open_info_request
    assert request is not None
    assert request.items == ("TAX_CERTIFICATE",)
    assert request.item_messages == {
        "TAX_CERTIFICATE": "Справка об отсутствии налоговой задолженности"
    }
    assert request.created_by == ANALYST
    decision = app.current_decision
    assert decision is not None
    assert decision.outcome is DecisionOutcome.NEEDS_INFO


def test_reject_keeps_reason_code() -> None:
    app = manual_review_application()

    reject(app, COMMENT)

    decision = app.current_decision
    assert decision is not None
    assert decision.outcome is DecisionOutcome.REJECTED
    assert decision.reasons == ("TAX_DEBT_OVER_10K",)


def test_recheck_adds_no_decision() -> None:
    app = manual_review_application()
    decisions = list(app.decisions)

    recheck(app, COMMENT)

    assert app.decisions == decisions
