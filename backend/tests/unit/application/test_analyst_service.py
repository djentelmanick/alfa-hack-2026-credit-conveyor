"""AnalystService: аудит каждого действия, публикация после commit, проверка ставки и вариантов."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.application.analyst_service import AnalystService, AuditAction, OfferTerms
from app.application.contract_service import ContractDocuments
from app.application.report_builder import ReportBuilder
from app.domain.decision import RatePolicy
from app.domain.entities import Application, TokenClaims
from app.domain.enums import ApplicationStatus, Role, SourceType, Variant
from app.domain.errors import InvalidStatus, SourcesNotReady, ValidationFailed
from app.domain.ports import AnalystFilter, PipelineTask
from app.domain.value_objects import Money
from app.infra.config.catalog import load_catalog
from app.infra.documents import ReportlabContractRenderer
from tests.fakes.analyst import manual_review_application
from tests.fakes.auth import FixedClock
from tests.fakes.context import make_application
from tests.fakes.pipeline import OFFER_TTL
from tests.fakes.runtime import RecordingPublisher, RecordingTaskQueue
from tests.fakes.scenarios import NOW
from tests.fakes.storage import InMemoryFileStorage
from tests.fakes.uow import InMemoryStore

S = ApplicationStatus
COMMENT = "Проверено по отчёту"
CLAIMS = TokenClaims(
    user_id=uuid4(),
    role=Role.ANALYST,
    name="Орлова Мария Игоревна",
    issued_at=NOW,
    expires_at=NOW,
    token_id="t",
)


class Harness:
    def __init__(self) -> None:
        self.store = InMemoryStore()
        self.publisher = RecordingPublisher(self.store)
        self.queue = RecordingTaskQueue()
        self.service = AnalystService(
            uow=self.store.uow,
            catalog=load_catalog(),
            rates=RatePolicy(),
            reports=ReportBuilder(),
            queue=self.queue,
            publisher=self.publisher,
            clock=FixedClock(),
            offer_ttl=OFFER_TTL,
            documents=ContractDocuments(
                renderer=ReportlabContractRenderer(),
                storage=InMemoryFileStorage(),
                bucket="credit-contracts",
                url_ttl=timedelta(minutes=5),
            ),
        )

    async def add(self, app: Application) -> Application:
        await self.store.applications.add(app)
        return app


@pytest.fixture
def h() -> Harness:
    return Harness()


BASE_TERMS = OfferTerms(
    product_code="LUMP_SUM",
    variant=Variant.UNSECURED,
    amount=Money.of(1_500_000),
    term_months=24,
    rate=Decimal("26.5"),
)


def _terms(**overrides: Any) -> OfferTerms:
    return replace(BASE_TERMS, **overrides)


async def test_approve_writes_audit_and_publishes_after_commit(h: Harness) -> None:
    app = await h.add(manual_review_application())

    result = await h.service.approve(CLAIMS, app.id, Decimal("23.5"), f" {COMMENT} ")

    assert result.status is S.APPROVED
    (entry,) = h.store.audit.entries
    assert (entry.action, entry.actor_id, entry.target_id) == (
        AuditAction.APPROVE,
        CLAIMS.user_id,
        app.id,
    )
    assert entry.details == {"comment": COMMENT, "rate": "23.5"}
    assert h.publisher.statuses() == ["APPROVED"]
    assert h.publisher.commits_at_publish == [1]


@pytest.mark.parametrize("rate", ["23.4", "29.6"])
async def test_approve_rate_outside_variant_range_is_rejected(h: Harness, rate: str) -> None:
    app = await h.add(manual_review_application())

    with pytest.raises(ValidationFailed) as exc:
        await h.service.approve(CLAIMS, app.id, Decimal(rate), COMMENT)

    assert [(e.field, e.code) for e in exc.value.details] == [("rate", "RATE_OUT_OF_RANGE")]
    assert app.status is S.MANUAL_REVIEW
    assert h.store.audit.entries == []
    assert h.store.commits == 0


async def test_approve_with_failed_source_writes_nothing(h: Harness) -> None:
    app = await h.add(manual_review_application(failed=[SourceType.FSSP]))

    with pytest.raises(SourcesNotReady):
        await h.service.approve(CLAIMS, app.id, Decimal("23.5"), COMMENT)

    assert h.store.audit.entries == []
    assert h.publisher.events == []


async def test_blank_comment_writes_nothing(h: Harness) -> None:
    app = await h.add(manual_review_application())

    with pytest.raises(ValidationFailed):
        await h.service.reject(CLAIMS, app.id, "TAX_DEBT_OVER_10K", "  ")

    assert h.store.audit.entries == []
    assert h.store.commits == 0


async def test_action_outside_manual_review_is_invalid_status(h: Harness) -> None:
    app = await h.add(make_application())

    with pytest.raises(InvalidStatus):
        await h.service.reject(CLAIMS, app.id, "TAX_DEBT_OVER_10K", COMMENT)

    assert h.store.audit.entries == []


async def test_counter_offer_is_audited_with_options(h: Harness) -> None:
    app = await h.add(manual_review_application())

    result = await h.service.counter_offer(CLAIMS, app.id, [_terms()], COMMENT)

    assert result.status is S.COUNTER_OFFER
    decision = result.current_decision
    assert decision is not None
    (offer,) = decision.offers
    assert (offer.amount, offer.term_months, offer.rate) == (
        Money.of(1_500_000),
        24,
        Decimal("26.5"),
    )
    (entry,) = h.store.audit.entries
    assert entry.action == AuditAction.COUNTER_OFFER
    assert entry.details == {
        "comment": COMMENT,
        "option_1": "LUMP_SUM UNSECURED 1500000.00 24 26.5",
    }


async def test_counter_offer_reports_every_bad_option_field(h: Harness) -> None:
    app = await h.add(manual_review_application())

    with pytest.raises(ValidationFailed) as exc:
        await h.service.counter_offer(
            CLAIMS,
            app.id,
            [
                _terms(),
                _terms(amount=Money.of(100_000), term_months=6, rate=Decimal("30")),
                _terms(product_code="REVOLVING_LINE"),
            ],
            COMMENT,
        )

    assert [(e.field, e.code) for e in exc.value.details] == [
        ("options[1].amount", "TERMS_IN_RANGE"),
        ("options[1].term_months", "TERMS_IN_RANGE"),
        ("options[1].rate", "RATE_OUT_OF_RANGE"),
        # Цель заявки DEVELOPMENT: кредитная линия под неё не выдаётся (products.yaml).
        ("options[2].product_code", "PURPOSE_ALLOWED"),
    ]
    assert app.status is S.MANUAL_REVIEW
    assert h.store.audit.entries == []


async def test_request_info_and_reject_are_audited(h: Harness) -> None:
    first = await h.add(manual_review_application())
    second = await h.add(manual_review_application())

    await h.service.request_info(CLAIMS, first.id, {"TAX_CERTIFICATE": "Справка"}, COMMENT)
    await h.service.reject(CLAIMS, second.id, "TAX_DEBT_OVER_10K", COMMENT)

    assert [(e.action, e.target_id, e.details) for e in h.store.audit.entries] == [
        (AuditAction.REQUEST_INFO, first.id, {"comment": COMMENT, "items": "TAX_CERTIFICATE"}),
        (AuditAction.REJECT, second.id, {"comment": COMMENT, "reason_code": "TAX_DEBT_OVER_10K"}),
    ]
    assert h.publisher.statuses() == ["NEEDS_INFO", "REJECTED"]


async def test_recheck_restarts_pipeline_after_commit(h: Harness) -> None:
    app = await h.add(manual_review_application(failed=[SourceType.BKI]))

    result = await h.service.recheck(CLAIMS, app.id, COMMENT)

    assert result.status is S.CHECKING
    assert result.pipeline_run_id is not None
    assert h.queue.tasks == [PipelineTask(application_id=app.id, run_id=result.pipeline_run_id)]
    (entry,) = h.store.audit.entries
    assert entry.action == AuditAction.RECHECK
    assert h.publisher.statuses() == ["CHECKING"]


async def test_queue_shows_manual_reasons_and_skips_drafts(h: Harness) -> None:
    waiting = await h.add(manual_review_application(reasons=("SECURED_VARIANT", "AMOUNT_OVER_5M")))
    await h.add(make_application())

    page = await h.service.queue(AnalystFilter(manual_queue=True), page=1, size=20)
    everything = await h.service.queue(AnalystFilter(), page=1, size=20)

    (entry,) = page.items
    assert entry.application.id == waiting.id
    assert entry.manual_reasons == ("SECURED_VARIANT", "AMOUNT_OVER_5M")
    assert entry.submitted_at == NOW
    assert everything.total == 1
