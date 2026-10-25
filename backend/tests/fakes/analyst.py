"""Заявки, дошедшие до ручной проверки, для тестов кабинета аналитика."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from app.domain.entities import Application, Decision, SourceSnapshot
from app.domain.enums import ApplicationStatus, DecisionOutcome, FetchStatus, SourceType
from tests.fakes.context import make_application
from tests.fakes.scenarios import NOW

S = ApplicationStatus

ALL_SOURCES = (
    SourceType.EGRUL,
    SourceType.BKI,
    SourceType.TURNOVER,
    SourceType.FNS,
    SourceType.FSSP,
    SourceType.CBR_KYC,
    SourceType.ROSFIN,
)


def snapshots(*, failed: Iterable[SourceType] = ()) -> list[SourceSnapshot]:
    broken = set(failed)
    return [
        SourceSnapshot(
            source=source,
            status=FetchStatus.FAILED if source in broken else FetchStatus.OK,
            attempts=3 if source in broken else 1,
            fetched_at=NOW,
        )
        for source in ALL_SOURCES
    ]


def manual_review_application(
    *,
    applicant_id: UUID | None = None,
    failed: Iterable[SourceType] = (),
    reasons: tuple[str, ...] = ("AMOUNT_OVER_5M",),
    **form: Any,
) -> Application:
    """Путь DRAFT → SUBMITTED → CHECKING → SCORING → MANUAL_REVIEW с системным решением."""
    app = make_application(**form)
    app.applicant_id = applicant_id or uuid4()
    app.transition_to(S.SUBMITTED, actor_id=app.applicant_id, reason="Заявка отправлена", now=NOW)
    app.transition_to(S.CHECKING, actor_id=None, reason="PIPELINE_STARTED", now=NOW)
    app.record_snapshots(snapshots(failed=failed))
    app.transition_to(S.SCORING, actor_id=None, reason="CHECKS_PASSED", now=NOW)
    app.add_decision(
        Decision(
            id=uuid4(),
            rule_code="MANUAL_REVIEW",
            outcome=DecisionOutcome.MANUAL_REVIEW,
            rate=None,
            reasons=reasons,
            decided_by=None,
            comment=None,
            decided_at=NOW,
        )
    )
    app.transition_to(S.MANUAL_REVIEW, actor_id=None, reason=", ".join(reasons), now=NOW)
    app.pull_events()
    return app
