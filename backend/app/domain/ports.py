"""Порты домена. Реализации живут в app/infra и подставляются в app/main.py."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from app.domain.entities import (
    Application,
    AuditEntry,
    Consent,
    Contract,
    User,
)
from app.domain.enums import KycLevel, SignatureStatus
from app.domain.events import StatusChangedEvent
from app.domain.value_objects import (
    BailiffInfo,
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


class PassportCheckPort(ABC):
    @abstractmethod
    async def is_valid(self, series: str, number: str) -> bool: ...


# --- Репозитории и единица работы ---


class ApplicationRepository(ABC):
    @abstractmethod
    async def get(self, application_id: UUID) -> Application:
        """Бросает NotFound."""

    @abstractmethod
    async def add(self, application: Application) -> None: ...

    @abstractmethod
    async def find_active_by_inn(self, inn: str) -> Application | None: ...


class ContractRepository(ABC):
    @abstractmethod
    async def get(self, contract_id: UUID) -> Contract:
        """Бросает NotFound."""

    @abstractmethod
    async def add(self, contract: Contract) -> None: ...


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: UUID) -> User:
        """Бросает NotFound."""

    @abstractmethod
    async def get_by_login(self, login: str) -> User | None: ...

    @abstractmethod
    async def add(self, user: User) -> None: ...


class AuditLogRepository(ABC):
    @abstractmethod
    async def add(self, entry: AuditEntry) -> None: ...


class UnitOfWork(ABC):
    applications: ApplicationRepository
    contracts: ContractRepository
    users: UserRepository
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


class StatusPublisher(ABC):
    @abstractmethod
    async def publish(self, event: StatusChangedEvent) -> None: ...


class TaskRunner(ABC):
    @abstractmethod
    def enqueue[*Ts](self, job: Callable[[*Ts], Awaitable[None]], *args: *Ts) -> None: ...


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
