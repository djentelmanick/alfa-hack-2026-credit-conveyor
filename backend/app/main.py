import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.errors import register_exception_handlers
from app.api.v1 import (
    analyst,
    applications,
    auth,
    catalog,
    companies,
    contracts,
    dev,
    emulation,
    health,
    ws,
)
from app.api.v1.deps import Container, Lifecycle
from app.application.analyst_service import AnalystService
from app.application.application_service import ApplicationService, StatementFiles
from app.application.auth_service import AuthService
from app.application.company_service import CompanyService
from app.application.consent_service import ConsentService
from app.application.contract_service import ContractDocuments, ContractService
from app.application.emulation_service import EmulationService
from app.application.expiration_job import ExpirationJob
from app.application.findings_builder import FindingsBuilder
from app.application.form_validator import FormValidator
from app.application.log_context import ApplicationIdFilter
from app.application.pipeline import DataCollector, PipelineOrchestrator, SourcePorts
from app.application.pipeline_reconciler import PipelineReconciler
from app.application.report_builder import ReportBuilder
from app.application.signing_service import SigningService
from app.domain.checks import (
    StatementVerifier,
    default_authenticity_strategies,
    default_manual_review_triggers,
    default_stop_rules,
)
from app.domain.decision import DecisionEngine, RatePolicy, default_decision_rules
from app.domain.events import ApplicationEvent
from app.domain.ports import Clock, StatusPublisher, TaskQueue, UnitOfWork
from app.domain.products import ProductCatalog
from app.domain.scoring import ScoringEngine, ScoringModel
from app.infra.clock import SystemClock
from app.infra.config.catalog import load_catalog
from app.infra.config.settings import Settings, get_settings
from app.infra.db.mock_failures import SqlMockFailures
from app.infra.db.pipeline_lock import PgAdvisoryPipelineLock
from app.infra.db.session import DatabaseProbe, create_engine, create_session_factory
from app.infra.db.uow import SqlUnitOfWork
from app.infra.documents import ReportlabContractRenderer
from app.infra.messaging import (
    EventSubscriber,
    PipelineConsumer,
    RabbitConnection,
    RabbitStatusPublisher,
    RabbitTaskQueue,
    declare_topology,
)
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockPassportAdapter,
    MockRosfinAdapter,
    MockSignatureAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from app.infra.parsers.statement_1c import Statement1CParser
from app.infra.realtime.ws_manager import WsConnectionManager
from app.infra.retrying import (
    RetryingAccountTurnover,
    RetryingBailiff,
    RetryingCbrKyc,
    RetryingCompanyRegistry,
    RetryingCreditBureau,
    RetryingRosfinmonitoring,
    RetryingSignatureVerifier,
    RetryingTaxService,
    RetryPolicy,
)
from app.infra.security.jwt import JwtService
from app.infra.security.passwords import Argon2PasswordHasher
from app.infra.storage.s3 import S3ClientFactory, S3FileStorage, S3Probe
from app.infra.tasks import AsyncioTaskRunner

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
EXPIRATION_INTERVAL_SEC = 60

type ContainerFactory = Callable[[Settings, AsyncEngine], Container]


@dataclass(frozen=True, slots=True)
class Mocks:
    """Синтетика, реестр поломок и источники за повторами: общие для api и worker."""

    fixtures: SyntheticData
    failures: MockFailures
    sources: SourcePorts
    retry: RetryPolicy
    delay_ms: int


def build_mocks(settings: Settings, failures: MockFailures) -> Mocks:
    fixtures = SyntheticData.load()
    delay = settings.pipeline_step_delay_ms
    retry = RetryPolicy(settings.source_retry_attempts, settings.source_retry_pause_ms)
    sources = SourcePorts(
        registry=RetryingCompanyRegistry(
            MockEgrulAdapter(fixtures, failures, delay_ms=delay), retry
        ),
        credit_bureau=RetryingCreditBureau(
            MockBkiAdapter(fixtures, failures, delay_ms=delay), retry
        ),
        turnover=RetryingAccountTurnover(
            MockTurnoverAdapter(fixtures, failures, delay_ms=delay), retry
        ),
        tax=RetryingTaxService(MockFnsAdapter(fixtures, failures, delay_ms=delay), retry),
        bailiff=RetryingBailiff(MockFsspAdapter(fixtures, failures, delay_ms=delay), retry),
        cbr=RetryingCbrKyc(MockCbrAdapter(fixtures, failures, delay_ms=delay), retry),
        rosfin=RetryingRosfinmonitoring(
            MockRosfinAdapter(fixtures, failures, delay_ms=delay), retry
        ),
    )
    return Mocks(fixtures=fixtures, failures=failures, sources=sources, retry=retry, delay_ms=delay)


def build_pipeline(
    settings: Settings,
    *,
    uow: Callable[[], UnitOfWork],
    engine: AsyncEngine,
    sources: SourcePorts,
    catalog: ProductCatalog,
    publisher: StatusPublisher,
    clock: Clock,
    scoring: ScoringModel | None = None,
) -> PipelineOrchestrator:
    verifier = StatementVerifier(default_authenticity_strategies())
    scoring = scoring or ScoringEngine(catalog)
    return PipelineOrchestrator(
        uow=uow,
        collector=DataCollector(
            sources, verifier, clock, retry_attempts=settings.source_retry_attempts
        ),
        stop_rules=default_stop_rules(catalog),
        verifier=verifier,
        scoring=scoring,
        decision=DecisionEngine(
            default_decision_rules(catalog, RatePolicy(), scoring),
            default_manual_review_triggers(),
        ),
        publisher=publisher,
        lock=PgAdvisoryPipelineLock(engine),
        clock=clock,
        offer_ttl=timedelta(days=settings.offer_ttl_days),
    )


@dataclass(frozen=True, slots=True)
class ApiMessaging:
    """RabbitMQ в api: публикация задач и событий и подписка на события для WebSocket.

    Конвейер и фоновые задачи api не выполняет: они в worker.
    """

    connection: RabbitConnection
    subscriber: EventSubscriber

    async def start(self) -> None:
        await self.connection.start()
        await _declare_topology(self.connection)
        await self.subscriber.start()

    async def stop(self) -> None:
        await self.subscriber.stop()
        await self.connection.close()


type EventSink = Callable[[ApplicationEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApiMessagingPorts:
    """Очередь задач, публикация событий и подписка на них для WebSocket.

    По умолчанию RabbitMQ; e2e подставляют in-memory шину и обработчик worker в том же процессе.
    """

    queue: TaskQueue
    publisher: StatusPublisher
    subscribe: Callable[[EventSink], Lifecycle]


def rabbit_messaging(settings: Settings) -> ApiMessagingPorts:
    connection = RabbitConnection(settings.rabbitmq_url)
    return ApiMessagingPorts(
        queue=RabbitTaskQueue(connection),
        publisher=RabbitStatusPublisher(connection),
        subscribe=lambda deliver: ApiMessaging(connection, EventSubscriber(connection, deliver)),
    )


async def _declare_topology(connection: RabbitConnection) -> None:
    channel = await connection.channel()
    try:
        await declare_topology(channel)
    finally:
        await channel.close()


def build_container(
    settings: Settings,
    engine: AsyncEngine,
    *,
    clock: Clock | None = None,
    messaging: ApiMessagingPorts | None = None,
) -> Container:
    """Composition root api: единственное место, где порты связываются с реализациями."""
    session_factory = create_session_factory(engine)

    def uow() -> UnitOfWork:
        return SqlUnitOfWork(session_factory)

    clock = clock or SystemClock()
    mocks = build_mocks(settings, SqlMockFailures(session_factory))
    fixtures, failures, retry, delay = mocks.fixtures, mocks.failures, mocks.retry, mocks.delay_ms
    registry, turnover = mocks.sources.registry, mocks.sources.turnover
    tokens = JwtService(
        settings.jwt_secret.get_secret_value(), timedelta(minutes=settings.jwt_ttl_minutes), clock
    )
    s3_factory = S3ClientFactory(settings)
    catalog = load_catalog()
    messaging = messaging or rabbit_messaging(settings)
    publisher, queue = messaging.publisher, messaging.queue
    realtime = WsConnectionManager(uow)
    offer_ttl = timedelta(days=settings.offer_ttl_days)
    storage = S3FileStorage.from_settings(settings)
    url_ttl = timedelta(seconds=settings.presigned_ttl_seconds)
    documents = ContractDocuments(
        renderer=ReportlabContractRenderer(),
        storage=storage,
        bucket=settings.s3_bucket_contracts,
        url_ttl=url_ttl,
    )
    contracts = ContractService(uow=uow, catalog=catalog, documents=documents, clock=clock)

    return Container(
        db_probe=DatabaseProbe(engine),
        s3_probe=S3Probe(s3_factory, [settings.s3_bucket_files, settings.s3_bucket_contracts]),
        tokens=tokens,
        catalog=catalog,
        auth=AuthService(
            uow=uow,
            hasher=Argon2PasswordHasher(),
            tokens=tokens,
            passports=MockPassportAdapter(fixtures, failures, delay_ms=delay),
            clock=clock,
        ),
        consents=ConsentService(uow=uow, registry=registry, clock=clock),
        companies=CompanyService(uow=uow, registry=registry, turnover=turnover),
        applications=ApplicationService(
            uow=uow,
            catalog=catalog,
            registry=registry,
            validator=FormValidator(catalog, registry, turnover),
            queue=queue,
            publisher=publisher,
            clock=clock,
            findings=FindingsBuilder(),
            statements=StatementFiles(
                storage=storage,
                signatures=RetryingSignatureVerifier(
                    MockSignatureAdapter(fixtures, failures, delay_ms=delay), retry
                ),
                parser=Statement1CParser(),
                bucket=settings.s3_bucket_files,
                max_bytes=settings.max_upload_mb * 1024 * 1024,
                url_ttl=url_ttl,
            ),
        ),
        analyst=AnalystService(
            uow=uow,
            catalog=catalog,
            rates=RatePolicy(),
            reports=ReportBuilder(),
            queue=queue,
            publisher=publisher,
            clock=clock,
            offer_ttl=offer_ttl,
            documents=documents,
        ),
        signing=SigningService(
            uow=uow,
            catalog=catalog,
            documents=documents,
            publisher=publisher,
            clock=clock,
            code_ttl=timedelta(seconds=settings.signing_code_ttl_seconds),
        ),
        contracts=contracts,
        emulation=EmulationService(uow=uow, contracts=contracts, documents=documents),
        realtime=realtime,
        mock_failures=failures,
        lifecycle=messaging.subscribe(realtime.deliver),
        demo_mode=settings.demo_mode,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Worker:
    """Процесс worker: конвейер из очереди, reconciler и истечение сроков предложений."""

    connection: RabbitConnection
    consumer: PipelineConsumer
    reconciler: PipelineReconciler
    expiration: ExpirationJob
    runner: AsyncioTaskRunner
    reconcile_interval_sec: float
    expiration_interval_sec: float = EXPIRATION_INTERVAL_SEC

    async def start(self) -> None:
        await self.connection.start()
        await _declare_topology(self.connection)
        # Сразу при старте: заявки, задачи которых потерялись, пока worker не работал.
        queued = await self.reconciler.run_once()
        logger.info("Reconciler при старте поставил задач: %s", queued)
        self.runner.every(self.reconcile_interval_sec, self.reconciler.run_once)
        self.runner.every(self.expiration_interval_sec, self.expiration.run_once)
        await self.consumer.start()

    async def stop(self) -> None:
        await self.consumer.stop()
        await self.runner.shutdown()
        await self.connection.close()


def build_worker(
    settings: Settings,
    engine: AsyncEngine,
    *,
    clock: Clock | None = None,
    scoring: ScoringModel | None = None,
) -> Worker:
    """Composition root worker."""
    session_factory = create_session_factory(engine)

    def uow() -> UnitOfWork:
        return SqlUnitOfWork(session_factory)

    clock = clock or SystemClock()
    mocks = build_mocks(settings, SqlMockFailures(session_factory))
    connection = RabbitConnection(settings.rabbitmq_url)
    publisher = RabbitStatusPublisher(connection)
    pipeline = build_pipeline(
        settings,
        uow=uow,
        engine=engine,
        sources=mocks.sources,
        catalog=load_catalog(),
        publisher=publisher,
        clock=clock,
        scoring=scoring,
    )
    return Worker(
        connection=connection,
        consumer=PipelineConsumer(
            connection, pipeline.run, pipeline.give_up, prefetch=settings.worker_prefetch
        ),
        reconciler=PipelineReconciler(
            uow=uow,
            queue=RabbitTaskQueue(connection),
            clock=clock,
            stale_after=timedelta(seconds=settings.pipeline_stale_after_sec),
        ),
        expiration=ExpirationJob(uow=uow, publisher=publisher, clock=clock),
        runner=AsyncioTaskRunner(),
        reconcile_interval_sec=settings.reconcile_interval_sec,
    )


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [application_id=%(application_id)s] %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, ApplicationIdFilter) for f in handler.filters):
            handler.addFilter(ApplicationIdFilter())


def create_app(
    settings: Settings | None = None, *, container_factory: ContainerFactory = build_container
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        container = container_factory(settings, engine)
        app.state.container = container
        await container.lifecycle.start()
        try:
            yield
        finally:
            await container.lifecycle.stop()
            await engine.dispose()

    app = FastAPI(title="Credit Conveyor API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Токен передаётся в заголовке Authorization, куки не используются.
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(catalog.router)
    api.include_router(companies.router)
    api.include_router(applications.router)
    api.include_router(analyst.router)
    api.include_router(analyst.contracts_router)
    api.include_router(contracts.router)
    api.include_router(emulation.router)
    api.include_router(ws.router)
    api.include_router(dev.router)
    app.include_router(api)
    return app
