from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.enums import (
    ApplicationStatus,
    CheckOutcome,
    CheckStage,
    ConsentType,
    DecisionOutcome,
    Direction,
    FetchStatus,
    Purpose,
    SignatureStatus,
    SourceType,
    Variant,
)
from app.domain.value_objects import Collateral, Money, Period


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicationForm:
    """Поля анкеты, которые клиент вводит и правит в DRAFT и NEEDS_INFO."""

    purpose: Purpose
    product_code: str
    variant: Variant
    amount: Money
    term_months: int
    owner_guarantee: bool = False
    collateral: Collateral | None = None


@dataclass(frozen=True, slots=True)
class Consent:
    type: ConsentType
    given_by: UUID
    given_at: datetime


@dataclass(frozen=True, slots=True)
class StatementOperation:
    date: date
    amount: Money
    direction: Direction
    counterparty_inn: str


@dataclass(frozen=True, kw_only=True, slots=True)
class BankStatement:
    id: UUID
    file_name: str
    file_key: str
    bank_name: str
    account_inn: str
    period: Period
    signature_status: SignatureStatus
    operations: tuple[StatementOperation, ...] = ()
    uploaded_at: datetime

    def months_covered(self) -> int:
        return self.period.months()


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceSnapshot:
    source: SourceType
    status: FetchStatus
    attempts: int
    payload: Mapping[str, object] = field(default_factory=dict)
    fetched_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class CheckResult:
    code: str
    stage: CheckStage
    expected: str
    actual: str
    outcome: CheckOutcome

    @property
    def passed(self) -> bool:
        return self.outcome is CheckOutcome.PASSED


@dataclass(frozen=True, kw_only=True, slots=True)
class OfferOption:
    id: UUID
    product_code: str
    variant: Variant
    amount: Money
    term_months: int
    rate: Decimal | None


@dataclass(frozen=True, kw_only=True, slots=True)
class Decision:
    id: UUID
    rule_code: str
    outcome: DecisionOutcome
    rate: Decimal | None
    reasons: tuple[str, ...] = ()
    offers: tuple[OfferOption, ...] = ()
    decided_by: UUID | None
    comment: str | None
    decided_at: datetime

    def offer(self, option_id: UUID) -> OfferOption | None:
        return next((o for o in self.offers if o.id == option_id), None)


@dataclass(kw_only=True, slots=True)
class InfoRequest:
    items: tuple[str, ...]
    message: str
    created_by: UUID | None
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class StatusTransition:
    from_status: ApplicationStatus
    to_status: ApplicationStatus
    actor_id: UUID | None
    reason: str
    at: datetime
