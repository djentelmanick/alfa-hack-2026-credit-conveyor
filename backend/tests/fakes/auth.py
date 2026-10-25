from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher as Argon2

from app.api.v1.deps import Container
from app.application.analyst_service import AnalystService
from app.application.application_service import ApplicationService, StatementFiles
from app.application.auth_service import AuthService
from app.application.company_service import CompanyService
from app.application.consent_service import ConsentService
from app.application.contract_service import ContractDocuments, ContractService
from app.application.emulation_service import EmulationService
from app.application.findings_builder import FindingsBuilder
from app.application.form_validator import FormValidator
from app.application.report_builder import ReportBuilder
from app.application.signing_service import SigningService
from app.domain.decision import RatePolicy
from app.domain.entities import User, UserCompany
from app.domain.enums import Role
from app.domain.ports import Clock, FileStoragePort, StatusPublisher, TaskQueue, UnitOfWork
from app.infra.config.catalog import load_catalog
from app.infra.documents import ReportlabContractRenderer
from app.infra.mocks import (
    MockEgrulAdapter,
    MockFailures,
    MockPassportAdapter,
    MockSignatureAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from app.infra.parsers.statement_1c import Statement1CParser
from app.infra.realtime.ws_manager import WsConnectionManager
from app.infra.security.jwt import JwtService
from app.infra.security.passwords import Argon2PasswordHasher
from tests.fakes.pipeline import OFFER_TTL
from tests.fakes.runtime import InMemoryEventBus, NoopLifecycle, RecordingTaskQueue
from tests.fakes.scenarios import NOW
from tests.fakes.storage import InMemoryFileStorage

JWT_SECRET = "test-secret-" + "x" * 52
TOKEN_TTL = timedelta(minutes=60)
PASSWORD = "correct-horse-battery"
STATEMENTS_BUCKET = "credit-files"
CONTRACTS_BUCKET = "credit-contracts"
MAX_STATEMENT_BYTES = 10 * 1024 * 1024


class FixedClock(Clock):
    def __init__(self, now: datetime = NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def fast_hasher() -> Argon2PasswordHasher:
    # Настоящий argon2, но с минимальной стоимостью: иначе каждый тест ждёт десятки миллисекунд.
    return Argon2PasswordHasher(Argon2(time_cost=1, memory_cost=8, parallelism=1))


def jwt_service(clock: Clock) -> JwtService:
    return JwtService(JWT_SECRET, TOKEN_TTL, clock)


async def make_user(
    *,
    role: Role = Role.CLIENT,
    login: str = "client",
    full_name: str = "Петров Алексей Николаевич",
    password: str = PASSWORD,
    companies: tuple[UserCompany, ...] = (),
) -> User:
    return User(
        id=uuid4(),
        login=login,
        password_hash=await fast_hasher().hash(password),
        role=role,
        full_name=full_name,
        passport_verified=True,
        created_at=NOW,
        companies=list(companies),
    )


class StubProbe:
    async def check(self) -> bool:
        return True


def make_container(
    uow: Callable[[], UnitOfWork],
    clock: Clock,
    *,
    fixtures: SyntheticData | None = None,
    failures: MockFailures | None = None,
    queue: TaskQueue | None = None,
    publisher: StatusPublisher | None = None,
    storage: FileStoragePort | None = None,
    demo_mode: bool = False,
) -> Container:
    """Тот же набор, что в main.build_container, но без задержек моков и с быстрым argon2.

    Конвейера в api нет: поставленные задачи копит RecordingTaskQueue, события идут
    через in-memory шину в WebSocket.
    """
    fixtures = fixtures or SyntheticData.load()
    failures = failures or MockFailures()
    registry = MockEgrulAdapter(fixtures, failures)
    turnover = MockTurnoverAdapter(fixtures, failures)
    tokens = jwt_service(clock)
    catalog = load_catalog()
    realtime = WsConnectionManager(uow)
    if publisher is None:
        bus = InMemoryEventBus()
        bus.subscribe(realtime.deliver)
        publisher = bus
    queue = queue or RecordingTaskQueue()
    storage = storage or InMemoryFileStorage()
    documents = ContractDocuments(
        renderer=ReportlabContractRenderer(),
        storage=storage,
        bucket=CONTRACTS_BUCKET,
        url_ttl=timedelta(minutes=5),
    )
    contracts = ContractService(uow=uow, catalog=catalog, documents=documents, clock=clock)
    return Container(
        db_probe=StubProbe(),
        s3_probe=StubProbe(),
        tokens=tokens,
        catalog=catalog,
        auth=AuthService(
            uow=uow,
            hasher=fast_hasher(),
            tokens=tokens,
            passports=MockPassportAdapter(fixtures, failures),
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
                signatures=MockSignatureAdapter(fixtures, failures),
                parser=Statement1CParser(),
                bucket=STATEMENTS_BUCKET,
                max_bytes=MAX_STATEMENT_BYTES,
                url_ttl=timedelta(minutes=5),
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
            offer_ttl=OFFER_TTL,
            documents=documents,
        ),
        signing=SigningService(
            uow=uow,
            catalog=catalog,
            documents=documents,
            publisher=publisher,
            clock=clock,
            code_ttl=timedelta(minutes=5),
        ),
        contracts=contracts,
        emulation=EmulationService(uow=uow, contracts=contracts, documents=documents),
        realtime=realtime,
        mock_failures=failures,
        lifecycle=NoopLifecycle(),
        demo_mode=demo_mode,
    )
