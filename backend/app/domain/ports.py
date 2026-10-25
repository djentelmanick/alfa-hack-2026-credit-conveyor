"""Порты домена. Реализации живут в app/infra и подставляются в app/main.py."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from app.domain.entities import (
    Application,
    AuditEntry,
    Consent,
    Contract,
    ContractVersion,
    ParsedStatement,
    TokenClaims,
    User,
)
from app.domain.enums import ApplicationStatus, KycLevel, RiskClass, SignatureStatus
from app.domain.events import ApplicationEvent
from app.domain.value_objects import (
    BailiffInfo,
    Collateral,
    CompanyProfile,
    CreditReport,
    Money,
    MonthlyTurnover,
    TaxInfo,
)

# --- Источники данных (сейчас только моки, BACKEND_SPEC раздел 10) ---


class CompanyRegistryPort(ABC):
    """ЕГРЮЛ / ЕГРИП."""

    @abstractmethod
    async def find_by_inn(self, inn: str) -> CompanyProfile: ...


class CreditBureauPort(ABC):
    """БКИ. Субъект это ИНН компании или физлица (руководитель, учредитель, ИП)."""

    @abstractmethod
    async def report(self, subject_inn: str, consent: Consent) -> CreditReport: ...


class AccountTurnoverPort(ABC):
    """Обороты по счёту в нашем банке."""

    @abstractmethod
    async def monthly_turnover(self, inn: str, months: int) -> MonthlyTurnover | None:
        """None, если счёта в нашем банке нет."""


class TaxServicePort(ABC):
    """ФНС, «Прозрачный бизнес» и ГИР БО."""

    @abstractmethod
    async def account_blocks(self, inn: str) -> bool: ...

    @abstractmethod
    async def is_mass_address(self, address: str) -> bool: ...

    @abstractmethod
    async def annual_revenue(self, inn: str, year: int) -> Money | None: ...

    @abstractmethod
    async def tax_info(self, inn: str) -> TaxInfo:
        """Массовость, дисквалификация и налоговый долг: этого нет в ЕГРЮЛ (SCOPE, раздел 4)."""


class BailiffPort(ABC):
    """ФССП."""

    @abstractmethod
    async def open_proceedings(self, inn: str) -> BailiffInfo: ...


class CbrKycPort(ABC):
    """Платформа ЦБ «Знай своего клиента»."""

    @abstractmethod
    async def risk_level(self, inn: str) -> KycLevel: ...


class RosfinmonitoringPort(ABC):
    @abstractmethod
    async def is_listed(self, inn: str, persons: Sequence[str]) -> bool: ...


class SignatureVerifierPort(ABC):
    """Проверка ЭП банка в выписке 1С."""

    @abstractmethod
    async def verify(self, statement_raw: bytes) -> SignatureStatus: ...


class StatementParser(ABC):
    """Разбор выписки, загруженной клиентом (сейчас формат 1CClientBankExchange)."""

    @abstractmethod
    def parse(self, raw: bytes) -> ParsedStatement:
        """Бросает ValidationFailed с полем file и кодом STATEMENT_FORMAT."""


class PassportCheckPort(ABC):
    @abstractmethod
    async def is_valid(self, series: str, number: str) -> bool: ...


# --- Репозитории и единица работы ---


@dataclass(frozen=True, kw_only=True, slots=True)
class AnalystFilter:
    """Фильтр списка аналитика. Черновики в него не попадают: банку их ещё не отправили."""

    manual_queue: bool = False
    status: ApplicationStatus | None = None
    inn: str | None = None
    risk_class: RiskClass | None = None


class ApplicationRepository(ABC):
    @abstractmethod
    async def get(self, application_id: UUID) -> Application:
        """Бросает NotFound."""

    @abstractmethod
    async def add(self, application: Application) -> None: ...

    @abstractmethod
    async def find_active_by_inn(self, inn: str) -> Application | None: ...

    @abstractmethod
    async def list_for_applicant(
        self,
        applicant_id: UUID,
        *,
        status: ApplicationStatus | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Application], int]:
        """Заявки клиента, новые сначала, и общее число без учёта offset и limit."""

    @abstractmethod
    async def list_by_statuses(self, statuses: Collection[ApplicationStatus]) -> list[Application]:
        """Все заявки в этих статусах, старые сначала."""

    @abstractmethod
    async def list_stale(
        self, statuses: Collection[ApplicationStatus], updated_before: datetime
    ) -> list[Application]:
        """Заявки в этих статусах, не менявшиеся с updated_before, старые сначала."""

    @abstractmethod
    async def list_expired(self, now: datetime) -> list[Application]:
        """Одобрения и контрпредложения, у которых expires_at уже наступил."""

    @abstractmethod
    async def list_for_analyst(
        self, flt: AnalystFilter, *, offset: int = 0, limit: int | None = None
    ) -> tuple[list[Application], int]:
        """Очередь ручной проверки идёт от старых к новым, остальной список от новых к старым."""


class ContractRepository(ABC):
    @abstractmethod
    async def get(self, contract_id: UUID) -> Contract:
        """Бросает NotFound."""

    @abstractmethod
    async def add(self, contract: Contract) -> None: ...

    @abstractmethod
    async def next_sequence(self) -> int:
        """Порядковый номер для номера договора. Номер, взятый в откаченной транзакции, теряется."""

    @abstractmethod
    async def list_for(
        self, *, applicant_id: UUID | None = None, application_id: UUID | None = None
    ) -> list[Contract]:
        """Договоры по фильтрам, новые сначала. Без фильтров это все договоры."""


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: UUID) -> User:
        """Бросает NotFound."""

    @abstractmethod
    async def get_by_login(self, login: str) -> User | None: ...

    @abstractmethod
    async def get_by_inn(self, inn: str) -> User | None: ...

    @abstractmethod
    async def add(self, user: User) -> None: ...


class ConsentRepository(ABC):
    """Согласия даются по ИНН ещё до заявки (/consents/give), заявка копирует их при создании."""

    @abstractmethod
    async def list_for(self, inn: str, user_id: UUID) -> list[Consent]: ...

    @abstractmethod
    async def add(self, inn: str, consent: Consent) -> None:
        """Повторное согласие того же типа заменяет прежнее."""


class AuditLogRepository(ABC):
    @abstractmethod
    async def add(self, entry: AuditEntry) -> None: ...

    @abstractmethod
    async def list_for(self, target_id: UUID) -> list[AuditEntry]:
        """Записи по объекту в порядке действий."""


class UnitOfWork(ABC):
    applications: ApplicationRepository
    contracts: ContractRepository
    users: UserRepository
    consents: ConsentRepository
    audit: AuditLogRepository

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Без явного commit изменения не сохраняются, даже если исключения не было.
        await self.rollback()

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...


# --- Инфраструктурные порты ---


class Clock(ABC):
    """Единственный источник текущего времени для домена и сервисов."""

    @abstractmethod
    def now(self) -> datetime:
        """Timezone-aware UTC."""

    def today(self) -> date:
        return self.now().date()


class TokenService(ABC):
    @property
    @abstractmethod
    def ttl(self) -> timedelta: ...

    @abstractmethod
    def issue(self, user: User) -> str: ...

    @abstractmethod
    def decode(self, token: str) -> TokenClaims:
        """Бросает TokenInvalid или TokenExpired."""


class PasswordHasher(ABC):
    """Хеширование намеренно медленное, поэтому методы асинхронные.

    Реализация выполняет его вне event loop.
    """

    @abstractmethod
    async def hash(self, password: str) -> str: ...

    @abstractmethod
    async def verify(self, password_hash: str, password: str) -> bool: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractDocumentData:
    """Что нужно для PDF версии договора, кроме самого договора."""

    contract: Contract
    version: ContractVersion
    product_name: str
    company_name: str
    inn: str
    collateral: Collateral | None


class ContractDocumentRenderer(ABC):
    @abstractmethod
    async def render(self, data: ContractDocumentData) -> bytes:
        """PDF выбранной версии договора."""


class StatusPublisher(ABC):
    @abstractmethod
    async def publish(self, event: ApplicationEvent) -> None:
        """Вызывается только после commit. Ошибка доставки не должна ронять вызывающего."""


@dataclass(frozen=True, slots=True)
class PipelineTask:
    """Задача конвейера. run_id отличает прогон после recheck от устаревших задач."""

    application_id: UUID
    run_id: UUID


class TaskQueue(ABC):
    @abstractmethod
    async def enqueue(self, task: PipelineTask) -> None:
        """Вызывается после commit. Бросает, если брокер недоступен: заявку подхватит reconciler."""


class PipelineLock(ABC):
    @abstractmethod
    def hold(self, application_id: UUID) -> AbstractAsyncContextManager[bool]:
        """False, если заявку уже ведёт другой обработчик."""


@dataclass(frozen=True, kw_only=True, slots=True)
class StoredFile:
    bucket: str
    key: str
    size: int
    content_type: str


class FileStoragePort(ABC):
    @abstractmethod
    async def put(self, bucket: str, key: str, data: bytes, content_type: str) -> StoredFile: ...

    @abstractmethod
    async def get(self, bucket: str, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, bucket: str, key: str) -> None: ...

    @abstractmethod
    async def presigned_get(self, bucket: str, key: str, ttl_sec: int, file_name: str) -> str: ...
