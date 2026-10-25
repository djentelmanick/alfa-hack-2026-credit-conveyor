import logging
from collections.abc import AsyncIterator, Callable
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
from app.api.v1.deps import Container
from app.application.analyst_service import AnalystService
from app.application.application_service import ApplicationService, StatementFiles
from app.application.auth_service import AuthService
from app.application.company_service import CompanyService
from app.application.consent_service import ConsentService
from app.application.contract_service import ContractDocuments, ContractService
from app.application.emulation_service import EmulationService
from app.application.expiration_job import ExpirationJob
from app.application.form_validator import FormValidator
from app.application.log_context import ApplicationIdFilter
from app.application.pipeline import DataCollector, PipelineOrchestrator, SourcePorts
from app.application.report_builder import ReportBuilder
from app.application.signing_service import SigningService
from app.domain.checks import (
    StatementVerifier,
    default_authenticity_strategies,
    default_manual_review_triggers,
    default_stop_rules,
)
from app.domain.decision import DecisionEngine, RatePolicy, default_decision_rules
from app.domain.ports import Clock, UnitOfWork
from app.domain.scoring import ScoringEngine, ScoringModel
from app.infra.clock import SystemClock
from app.infra.config.catalog import load_catalog
from app.infra.config.settings import Settings, get_settings
from app.infra.db.session import DatabaseProbe, create_engine, create_session_factory
from app.infra.db.uow import SqlUnitOfWork
from app.infra.documents import ReportlabContractRenderer
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

API_PREFIX = "/api/v1"
EXPIRATION_INTERVAL_SEC = 60

type ContainerFactory = Callable[[Settings, AsyncEngine], Container]


@dataclass(frozen=True, slots=True)
class BackgroundServices:
    runner: AsyncioTaskRunner
    pipeline: PipelineOrchestrator
    expiration: ExpirationJob
    expiration_interval_sec: float = EXPIRATION_INTERVAL_SEC

    async def start(self) -> None:
        # Процесс один: заявки, оставшиеся в SUBMITTED, CHECKING или SCORING,
        # после рестарта никто не ведёт.
        await self.pipeline.recover(self.runner)
        self.runner.every(self.expiration_interval_sec, self.expiration.run_once)

    async def stop(self) -> None:
        await self.runner.shutdown()


def build_container(
    settings: Settings,
    engine: AsyncEngine,
    *,
    clock: Clock | None = None,
    scoring: ScoringModel | None = None,
) -> Container:
    """Composition root: единственное место, где порты связываются с реализациями.

    clock и scoring подменяют e2e-тесты: конвейер идёт в фоне,
    и dependency_overrides до него не достаёт.
    """
    session_factory = create_session_factory(engine)

    def uow() -> UnitOfWork:
        return SqlUnitOfWork(session_factory)

    clock = clock or SystemClock()
    fixtures = SyntheticData.load()
    failures = MockFailures()
    delay = settings.pipeline_step_delay_ms
    retry = RetryPolicy(settings.source_retry_attempts, settings.source_retry_pause_ms)
    registry = RetryingCompanyRegistry(MockEgrulAdapter(fixtures, failures, delay_ms=delay), retry)
    turnover = RetryingAccountTurnover(
        MockTurnoverAdapter(fixtures, failures, delay_ms=delay), retry
    )
    sources = SourcePorts(
        registry=registry,
        credit_bureau=RetryingCreditBureau(
            MockBkiAdapter(fixtures, failures, delay_ms=delay), retry
        ),
        turnover=turnover,
        tax=RetryingTaxService(MockFnsAdapter(fixtures, failures, delay_ms=delay), retry),
        bailiff=RetryingBailiff(MockFsspAdapter(fixtures, failures, delay_ms=delay), retry),
        cbr=RetryingCbrKyc(MockCbrAdapter(fixtures, failures, delay_ms=delay), retry),
        rosfin=RetryingRosfinmonitoring(
            MockRosfinAdapter(fixtures, failures, delay_ms=delay), retry
        ),
    )
    tokens = JwtService(
        settings.jwt_secret.get_secret_value(), timedelta(minutes=settings.jwt_ttl_minutes), clock
    )
    s3_factory = S3ClientFactory(settings)
    catalog = load_catalog()
    realtime = WsConnectionManager(uow)
    runner = AsyncioTaskRunner()
    verifier = StatementVerifier(default_authenticity_strategies())
    rates = RatePolicy()
    offer_ttl = timedelta(days=settings.offer_ttl_days)
    pipeline = PipelineOrchestrator(
        uow=uow,
        collector=DataCollector(
            sources, verifier, clock, retry_attempts=settings.source_retry_attempts
        ),
        stop_rules=default_stop_rules(catalog),
        verifier=verifier,
        scoring=scoring or ScoringEngine(),
        decision=DecisionEngine(
            default_decision_rules(catalog, rates), default_manual_review_triggers()
        ),
        publisher=realtime,
        clock=clock,
        offer_ttl=offer_ttl,
    )
    expiration = ExpirationJob(uow=uow, publisher=realtime, clock=clock)
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
            runner=runner,
            pipeline=pipeline.run,
            publisher=realtime,
            clock=clock,
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
            rates=rates,
            reports=ReportBuilder(),
            runner=runner,
            pipeline=pipeline.run,
            publisher=realtime,
            clock=clock,
            offer_ttl=offer_ttl,
            documents=documents,
        ),
        signing=SigningService(
            uow=uow,
            catalog=catalog,
            documents=documents,
            publisher=realtime,
            clock=clock,
            code_ttl=timedelta(seconds=settings.signing_code_ttl_seconds),
        ),
        contracts=contracts,
        emulation=EmulationService(uow=uow, contracts=contracts, documents=documents),
        expiration=expiration,
        realtime=realtime,
        mock_failures=failures,
        lifecycle=BackgroundServices(runner, pipeline, expiration),
        demo_mode=settings.demo_mode,
    )


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [application_id=%(application_id)s] %(message)s"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, ApplicationIdFilter) for f in handler.filters):
            handler.addFilter(ApplicationIdFilter())


def create_app(
    settings: Settings | None = None, *, container_factory: ContainerFactory = build_container
) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging()

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
