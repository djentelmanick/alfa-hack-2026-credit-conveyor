from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import IndustryRisk


@dataclass(frozen=True)
class ScoringConfig:
    # Возраст бизнеса
    age_young_months: int = 12
    age_mid_months: int = 36
    age_young_points: int = 0
    age_mid_points: int = 10
    age_mature_points: int = 20

    # Долговая нагрузка
    debt_low_pct: Decimal = Decimal("0.20")
    debt_high_pct: Decimal = Decimal("0.40")
    debt_low_points: int = 25
    debt_mid_points: int = 10
    debt_high_points: int = -15

    # Сумма заявки / годовая выручка
    ltr_low_pct: Decimal = Decimal("0.25")
    ltr_high_pct: Decimal = Decimal("0.50")
    ltr_low_points: int = 15
    ltr_mid_points: int = 5
    ltr_high_points: int = -10

    # Кредитная история
    credit_clean_points: int = 20
    credit_no_history_points: int = 0
    credit_minor_overdue_points: int = -10
    credit_major_overdue_points: int = -20

    # Закрытые кредиты
    closed_credits_yes_points: int = 10
    closed_credits_no_points: int = 0

    # Отраслевой риск
    industry_low_points: int = 10
    industry_mid_points: int = 0
    industry_high_points: int = -10

    # Динамика выручки
    revenue_drop_pct: Decimal = Decimal("0.30")
    revenue_drop_points: int = -15
    revenue_ok_points: int = 0

    # Адрес массовой регистрации
    mass_address_points: int = -10
    no_mass_address_points: int = 0

    # Массовый руководитель / учредитель
    mass_director_points: int = -10
    no_mass_director_points: int = 0

    # Обращения за кредитами
    inquiries_threshold: int = 3
    inquiries_high_points: int = -10
    inquiries_ok_points: int = 0

    # Границы классов риска
    class_a_min: int = 70
    class_b_min: int = 50
    class_c_min: int = 30

    max_score: int = 100


DEFAULT_CONFIG = ScoringConfig()


def _build_okved_risk_map() -> dict[str, IndustryRisk]:
    m: dict[str, IndustryRisk] = {}

    # Низкий: производство (10–33), IT (62, 63), медицина (86)
    for i in range(10, 34):
        m[str(i)] = IndustryRisk.LOW
    m["62"] = IndustryRisk.LOW
    m["63"] = IndustryRisk.LOW
    m["86"] = IndustryRisk.LOW

    # Высокий: строительство (41–43), туризм (79), грузоперевозки (49.4)
    for i in range(41, 44):
        m[str(i)] = IndustryRisk.HIGH
    m["79"] = IndustryRisk.HIGH
    m["49.4"] = IndustryRisk.HIGH

    return m


OKVED_RISK_MAP: dict[str, IndustryRisk] = _build_okved_risk_map()


def get_industry_risk(okved_code: str) -> IndustryRisk:
    code = okved_code
    while code:
        if code in OKVED_RISK_MAP:
            return OKVED_RISK_MAP[code]
        code = code[:-1].rstrip(".")
    return IndustryRisk.MEDIUM
