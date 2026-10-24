from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Self

from app.domain.dates import full_months_between
from app.domain.enums import CollateralType, CompanyKind, CompanyStatus

if TYPE_CHECKING:
    from app.domain.entities.user import User

_KOPECK = Decimal("0.01")


def _to_decimal(value: object) -> Decimal:
    # bool это подкласс int, а float теряет копейки, поэтому оба запрещены явно.
    if isinstance(value, bool | float) or not isinstance(value, Decimal | int):
        raise TypeError(f"Ожидается Decimal или int, получено {type(value).__name__}")
    return Decimal(value)


@dataclass(frozen=True, slots=True, order=True)
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        value = _to_decimal(self.amount)
        if not value.is_finite():
            raise ValueError("Сумма должна быть конечным числом")
        object.__setattr__(self, "amount", value.quantize(_KOPECK, rounding=ROUND_HALF_UP))

    @classmethod
    def of(cls, value: Decimal | int | str) -> Self:
        return cls(Decimal(value) if isinstance(value, str) else _to_decimal(value))

    @classmethod
    def zero(cls) -> Self:
        return cls(Decimal(0))

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount - other.amount)

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.amount * _to_decimal(factor))

    def __truediv__(self, divisor: Decimal | int) -> Money:
        return Money(self.amount / _to_decimal(divisor))

    def percent(self, pct: Decimal) -> Money:
        return Money(self.amount * _to_decimal(pct) / 100)

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_positive(self) -> bool:
        return self.amount > 0

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class Period:
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise ValueError("Конец периода раньше начала")

    def months(self) -> int:
        """Полные месяцы с учётом последнего дня: 1 января – 31 декабря это 12 месяцев."""
        return full_months_between(self.date_from, self.date_to + timedelta(days=1))


@dataclass(frozen=True, slots=True)
class Collateral:
    type: CollateralType
    description: str
    estimated_value: Money

    def covers(self, amount: Money, min_pct: Decimal) -> bool:
        return self.estimated_value >= amount.percent(min_pct)


@dataclass(frozen=True, slots=True)
class Founder:
    full_name: str
    inn: str
    share_pct: Decimal


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    inn: str
    kind: CompanyKind
    name: str
    ogrn: str
    registered_at: date
    status: CompanyStatus
    okved: str
    director: str
    director_inn: str
    address: str
    founders: tuple[Founder, ...] = ()
    director_changed_at: date | None = None
    unreliable_records: tuple[str, ...] = ()

    def is_director(self, user: User) -> bool:
        return user.full_name.casefold() == self.director.casefold()

    def age_months(self, today: date) -> int:
        return full_months_between(self.registered_at, today)


@dataclass(frozen=True, slots=True)
class CreditReport:
    """Ответ БКИ по компании или физлицу (руководитель, учредитель, ИП)."""

    subject_inn: str
    has_history: bool
    current_overdue_days: int
    max_closed_overdue_days_last_year: int
    monthly_payments: Money
    has_closed_loans_without_overdue: bool
    inquiries_last_3_months: int


@dataclass(frozen=True, slots=True)
class MonthRevenue:
    month: date
    incoming: Money


@dataclass(frozen=True, slots=True)
class MonthlyTurnover:
    inn: str
    months: tuple[MonthRevenue, ...] = field(default_factory=tuple)

    def total(self) -> Money:
        return sum((m.incoming for m in self.months), Money.zero())


@dataclass(frozen=True, slots=True)
class TaxInfo:
    """Данные «Прозрачного бизнеса» ФНС: только то, чего нет в ЕГРЮЛ."""

    inn: str
    accounts_blocked: bool
    mass_address: bool
    mass_director: bool
    mass_founder: bool
    director_disqualified: bool
    tax_debt: Money
    data_date: date


@dataclass(frozen=True, slots=True)
class BailiffInfo:
    subject_inn: str
    open_proceedings: int
    total_debt: Money
