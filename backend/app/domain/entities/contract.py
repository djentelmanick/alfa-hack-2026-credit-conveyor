from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.dates import add_months
from app.domain.enums import ContractStatus, OperationType, Variant
from app.domain.value_objects import Money


@dataclass(frozen=True, kw_only=True, slots=True)
class ScheduleItem:
    due_date: date
    principal: Money
    interest: Money
    balance: Money

    @property
    def payment(self) -> Money:
        return self.principal + self.interest


@dataclass(kw_only=True, slots=True)
class Tranche:
    id: UUID
    amount: Money
    issued_at: date
    due_at: date
    outstanding: Money


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractVersion:
    version_no: int
    document_key: str
    changes: Mapping[str, str] = field(default_factory=dict)
    schedule: tuple[ScheduleItem, ...] = ()
    author_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class Operation:
    id: UUID
    type: OperationType
    amount: Money
    contract_date: date
    actor_id: UUID | None
    balance_after: Money


@dataclass(kw_only=True, eq=False)
class Contract(ABC):
    id: UUID
    application_id: UUID
    number: str
    product_code: str
    variant: Variant
    rate: Decimal
    term_months: int
    start_date: date
    contract_date: date
    status: ContractStatus = ContractStatus.ACTIVE
    versions: list[ContractVersion] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)

    @property
    def end_date(self) -> date:
        return add_months(self.start_date, self.term_months)

    def is_active(self) -> bool:
        return self.status is ContractStatus.ACTIVE

    @abstractmethod
    def debt(self) -> Money: ...


@dataclass(kw_only=True, eq=False)
class LoanContract(Contract):
    principal: Money
    outstanding: Money

    def debt(self) -> Money:
        return self.outstanding


@dataclass(kw_only=True, eq=False)
class CreditLineContract(Contract):
    limit: Money
    available: Money
    tranches: list[Tranche] = field(default_factory=list)

    def debt(self) -> Money:
        return sum((t.outstanding for t in self.tranches), Money.zero())
