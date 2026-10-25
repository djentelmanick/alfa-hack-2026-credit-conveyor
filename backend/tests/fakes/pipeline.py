"""Конвейер на моках без задержек: тот же набор, что в main.build_container."""

from collections.abc import Callable
from datetime import timedelta

from app.application.expiration_job import ExpirationJob
from app.application.pipeline import DataCollector, PipelineOrchestrator, SourcePorts
from app.domain.checks import (
    StatementVerifier,
    default_authenticity_strategies,
    default_manual_review_triggers,
    default_stop_rules,
)
from app.domain.decision import DecisionEngine, RatePolicy, default_decision_rules
from app.domain.ports import Clock, PipelineLock, StatusPublisher, UnitOfWork
from app.domain.scoring import ScoringModel
from app.infra.config.catalog import load_catalog
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockRosfinAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from app.infra.retrying import (
    RetryingAccountTurnover,
    RetryingBailiff,
    RetryingCbrKyc,
    RetryingCompanyRegistry,
    RetryingCreditBureau,
    RetryingRosfinmonitoring,
    RetryingTaxService,
    RetryPolicy,
)
from tests.fakes.runtime import InMemoryPipelineLock

RETRY_ATTEMPTS = 3
OFFER_TTL = timedelta(days=7)


def source_ports(fixtures: SyntheticData, failures: MockFailures) -> SourcePorts:
    retry = RetryPolicy(RETRY_ATTEMPTS, 0)
    return SourcePorts(
        registry=RetryingCompanyRegistry(MockEgrulAdapter(fixtures, failures), retry),
        credit_bureau=RetryingCreditBureau(MockBkiAdapter(fixtures, failures), retry),
        turnover=RetryingAccountTurnover(MockTurnoverAdapter(fixtures, failures), retry),
        tax=RetryingTaxService(MockFnsAdapter(fixtures, failures), retry),
        bailiff=RetryingBailiff(MockFsspAdapter(fixtures, failures), retry),
        cbr=RetryingCbrKyc(MockCbrAdapter(fixtures, failures), retry),
        rosfin=RetryingRosfinmonitoring(MockRosfinAdapter(fixtures, failures), retry),
    )


def make_pipeline(
    *,
    uow: Callable[[], UnitOfWork],
    clock: Clock,
    publisher: StatusPublisher,
    scoring: ScoringModel,
    fixtures: SyntheticData,
    failures: MockFailures,
    lock: PipelineLock | None = None,
) -> PipelineOrchestrator:
    catalog = load_catalog()
    verifier = StatementVerifier(default_authenticity_strategies())
    return PipelineOrchestrator(
        uow=uow,
        collector=DataCollector(
            source_ports(fixtures, failures), verifier, clock, retry_attempts=RETRY_ATTEMPTS
        ),
        stop_rules=default_stop_rules(catalog),
        verifier=verifier,
        scoring=scoring,
        decision=DecisionEngine(
            default_decision_rules(catalog, RatePolicy()), default_manual_review_triggers()
        ),
        publisher=publisher,
        lock=lock or InMemoryPipelineLock(),
        clock=clock,
        offer_ttl=OFFER_TTL,
    )


def make_expiration_job(
    uow: Callable[[], UnitOfWork], clock: Clock, publisher: StatusPublisher
) -> ExpirationJob:
    return ExpirationJob(uow=uow, publisher=publisher, clock=clock)
