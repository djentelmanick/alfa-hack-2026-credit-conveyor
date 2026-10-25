from collections import defaultdict
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
from app.domain.value_objects import Collateral, Money, MonthlyTurnover, MonthRevenue, Period


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

    def last_operation_date(self) -> date | None:
        return max((op.date for op in self.operations), default=None)

    def incoming_total(self) -> Money:
        return sum(
            (op.amount for op in self.operations if op.direction is Direction.INCOMING),
            Money.zero(),
        )

    def annual_revenue(self) -> Money:
        """Поступления за период, приведённые к году (SCOPE, раздел 3)."""
        # Выписка короче месяца уйдёт в дозапрос, деление на ноль ей не нужно.
        return self.incoming_total() * 12 / max(self.months_covered(), 1)


@dataclass(frozen=True, kw_only=True, slots=True)
class ParsedStatement:
    """Результат разбора файла выписки.

    BankStatement из него собирает сервис загрузки: id, ключ S3 и время загрузки знает только он.
    """

    bank_name: str
    account: str
    account_inn: str
    period: Period
    operations: tuple[StatementOperation, ...]

    @property
    def months_covered(self) -> int:
        return self.period.months()

    @property
    def last_operation_date(self) -> date:
        return max(op.date for op in self.operations)

    def incoming(self) -> tuple[StatementOperation, ...]:
        return tuple(op for op in self.operations if op.direction is Direction.INCOMING)

    def monthly_turnover(self) -> MonthlyTurnover:
        by_month: dict[date, Money] = defaultdict(Money.zero)
        for op in self.incoming():
            month = op.date.replace(day=1)
            by_month[month] = by_month[month] + op.amount
        return MonthlyTurnover(
            inn=self.account_inn,
            months=tuple(MonthRevenue(month=m, incoming=by_month[m]) for m in sorted(by_month)),
        )

    def annual_revenue(self) -> Money:
        """Поступления за период, приведённые к году (SCOPE, раздел 3)."""
        total = sum((op.amount for op in self.incoming()), Money.zero())
        # Выписка короче месяца всё равно уйдёт в дозапрос, деление на ноль ей не нужно.
        return total * 12 / max(self.months_covered, 1)

    def to_statement(
        self,
        *,
        id: UUID,
        file_name: str,
        file_key: str,
        signature_status: SignatureStatus,
        uploaded_at: datetime,
    ) -> BankStatement:
        return BankStatement(
            id=id,
            file_name=file_name,
            file_key=file_key,
            bank_name=self.bank_name,
            account_inn=self.account_inn,
            period=self.period,
            signature_status=signature_status,
            operations=self.operations,
            uploaded_at=uploaded_at,
        )


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
    rate: Decimal


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


@dataclass(frozen=True, kw_only=True, slots=True)
class SigningCode:
    """Код подтверждения подписи. Показывается прямо в интерфейсе (SCOPE, раздел 9)."""

    code: str
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


@dataclass(kw_only=True, slots=True)
class InfoRequest:
    items: tuple[str, ...]
    message: str
    created_by: UUID | None
    created_at: datetime
    resolved_at: datetime | None = None
    # Пояснения аналитика по коду пункта. У системного дозапроса пусто, подпись из словаря.
    item_messages: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, slots=True)
class StatusTransition:
    from_status: ApplicationStatus
    to_status: ApplicationStatus
    actor_id: UUID | None
    reason: str
    at: datetime
