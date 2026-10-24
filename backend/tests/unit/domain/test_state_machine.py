from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest

from app.domain.entities import Application
from app.domain.enums import (
    AllowedAction,
    ApplicationStatus,
    CompanyKind,
    Purpose,
    Role,
    Variant,
)
from app.domain.errors import InvalidStatus
from app.domain.state_machine import ApplicationStateMachine
from app.domain.value_objects import Money

S = ApplicationStatus

# Таблица из BACKEND_SPEC, раздел 5. Переписана руками, а не взята из кода,
# чтобы тест ловил расхождение.
ALLOWED: frozenset[tuple[ApplicationStatus, ApplicationStatus]] = frozenset(
    {
        (S.DRAFT, S.SUBMITTED),
        (S.DRAFT, S.CANCELLED),
        (S.SUBMITTED, S.CHECKING),
        (S.CHECKING, S.REJECTED),
        (S.CHECKING, S.NEEDS_INFO),
        (S.CHECKING, S.MANUAL_REVIEW),
        (S.CHECKING, S.SCORING),
        (S.NEEDS_INFO, S.SUBMITTED),
        (S.NEEDS_INFO, S.CANCELLED),
        (S.SCORING, S.APPROVED),
        (S.SCORING, S.COUNTER_OFFER),
        (S.SCORING, S.MANUAL_REVIEW),
        (S.SCORING, S.REJECTED),
        (S.MANUAL_REVIEW, S.APPROVED),
        (S.MANUAL_REVIEW, S.COUNTER_OFFER),
        (S.MANUAL_REVIEW, S.NEEDS_INFO),
        (S.MANUAL_REVIEW, S.REJECTED),
        (S.MANUAL_REVIEW, S.CHECKING),
        (S.APPROVED, S.SIGNING),
        (S.APPROVED, S.CANCELLED),
        (S.COUNTER_OFFER, S.SIGNING),
        (S.COUNTER_OFFER, S.NEEDS_INFO),
        (S.COUNTER_OFFER, S.CANCELLED),
        (S.SIGNING, S.ISSUED),
        (S.SIGNING, S.CANCELLED),
    }
)
FORBIDDEN = sorted(pair for pair in product(S, S) if pair not in ALLOWED)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _application(status: ApplicationStatus) -> Application:
    return Application(
        id=uuid4(),
        applicant_id=uuid4(),
        inn="7701234567",
        company_kind=CompanyKind.LLC,
        purpose=Purpose.DEVELOPMENT,
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        amount=Money.of("2000000"),
        term_months=12,
        owner_guarantee=True,
        status=status,
        created_at=NOW,
    )


@pytest.mark.parametrize(("source", "target"), sorted(ALLOWED))
def test_transition_from_table_is_allowed(
    source: ApplicationStatus, target: ApplicationStatus
) -> None:
    assert ApplicationStateMachine.can_transition(source, target)
    assert ApplicationStateMachine.transition(source, target) is target


@pytest.mark.parametrize(("source", "target"), FORBIDDEN)
def test_transition_outside_table_raises_invalid_status(
    source: ApplicationStatus, target: ApplicationStatus
) -> None:
    assert not ApplicationStateMachine.can_transition(source, target)
    with pytest.raises(InvalidStatus):
        ApplicationStateMachine.transition(source, target)


@pytest.mark.parametrize(("source", "target"), sorted(ALLOWED))
def test_application_follows_every_allowed_transition(
    source: ApplicationStatus, target: ApplicationStatus
) -> None:
    app = _application(source)

    app.transition_to(target, actor_id=None, reason="test", now=NOW, offer_ttl=timedelta(days=7))

    assert app.status is target


@pytest.mark.parametrize(("source", "target"), FORBIDDEN)
def test_application_rejects_forbidden_transition_and_keeps_state(
    source: ApplicationStatus, target: ApplicationStatus
) -> None:
    app = _application(source)

    with pytest.raises(InvalidStatus):
        app.transition_to(
            target, actor_id=None, reason="test", now=NOW, offer_ttl=timedelta(days=7)
        )

    assert app.status is source
    assert app.history == []
    assert app.pull_events() == []


def test_status_cannot_be_assigned_directly() -> None:
    app = _application(S.DRAFT)

    with pytest.raises(AttributeError):
        app.status = S.ISSUED


@pytest.mark.parametrize(
    ("status", "active"),
    [(status, status not in {S.ISSUED, S.REJECTED, S.CANCELLED}) for status in S],
)
def test_active_statuses_are_all_except_final(status: ApplicationStatus, active: bool) -> None:
    assert ApplicationStateMachine.is_active(status) is active
    assert _application(status).is_active() is active


A = AllowedAction
CLIENT_ACTIONS: dict[ApplicationStatus, set[AllowedAction]] = {
    S.DRAFT: {A.EDIT, A.UPLOAD_STATEMENT, A.SUBMIT, A.CANCEL},
    S.NEEDS_INFO: {A.EDIT, A.UPLOAD_STATEMENT, A.SUBMIT, A.CANCEL},
    S.APPROVED: {A.ACCEPT, A.CANCEL},
    S.COUNTER_OFFER: {A.ACCEPT, A.CANCEL},
    S.SIGNING: {A.SIGN, A.CANCEL},
}


@pytest.mark.parametrize("status", list(S))
def test_allowed_actions_for_client(status: ApplicationStatus) -> None:
    actions = ApplicationStateMachine.allowed_actions(_application(status), Role.CLIENT)

    assert set(actions) == CLIENT_ACTIONS.get(status, set())


@pytest.mark.parametrize("status", list(S))
def test_client_actions_are_backed_by_transitions(status: ApplicationStatus) -> None:
    actions = ApplicationStateMachine.allowed_actions(_application(status), Role.CLIENT)

    if A.CANCEL in actions:
        assert ApplicationStateMachine.can_transition(status, S.CANCELLED)
    if A.SUBMIT in actions:
        assert ApplicationStateMachine.can_transition(status, S.SUBMITTED)
    if A.SIGN in actions:
        assert ApplicationStateMachine.can_transition(status, S.ISSUED)


@pytest.mark.parametrize("status", list(S))
def test_analyst_has_no_client_actions(status: ApplicationStatus) -> None:
    assert ApplicationStateMachine.allowed_actions(_application(status), Role.ANALYST) == []
