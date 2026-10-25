from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class RiskClass(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class IndustryRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CreditHistoryStatus(str, Enum):
    """Худшее применимое условие по кредитной истории за последний год."""

    CLEAN = "clean"
    NO_HISTORY = "no_history"
    MINOR_OVERDUE = "minor_overdue"  # закрытые просрочки до 30 дней
    MAJOR_OVERDUE = "major_overdue"  # закрытые >30 дней или текущая до 30 дней


@dataclass(frozen=True)
class ScoringInput:
    # Компания
    business_age_months: int
    okved_code: str
    is_mass_address: bool
    is_mass_director_or_founder: bool

    # Кредитная история
    credit_history_status: CreditHistoryStatus
    has_closed_credits_without_overdue: bool
    credit_inquiries_3_months: int

    # Финансы
    monthly_revenue: Decimal
    existing_monthly_payments: Decimal

    # Заявка
    requested_amount: Decimal
    requested_term_months: int
    min_annual_rate: Decimal

    # Динамика выручки (None = недостаточно данных, < 12 мес. истории)
    last_2q_revenue: Decimal | None = None
    prev_2q_revenue: Decimal | None = None


@dataclass(frozen=True)
class FactorResult:
    name: str
    points: int
    max_points: int
    value: str
    description: str
    explanation: str


@dataclass(frozen=True)
class ScoringResult:
    factors: list[FactorResult]
    total_score: int
    max_score: int
    risk_class: RiskClass
