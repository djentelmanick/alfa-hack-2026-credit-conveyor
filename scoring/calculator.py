from __future__ import annotations

from decimal import Decimal

from .config import DEFAULT_CONFIG, ScoringConfig, get_industry_risk
from .models import (
    CreditHistoryStatus,
    FactorResult,
    IndustryRisk,
    RiskClass,
    ScoringInput,
    ScoringResult,
)


def calculate_annuity_payment(
    principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
) -> Decimal:
    if term_months <= 0:
        return principal
    if annual_rate <= 0:
        return principal / term_months
    r = float(annual_rate) / 12
    n = term_months
    power = (1 + r) ** n
    payment = float(principal) * r * power / (power - 1)
    return Decimal(str(round(payment, 2)))


def calculate_score(
    inp: ScoringInput,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> ScoringResult:
    factors = [
        _business_age(inp, config),
        _debt_load(inp, config),
        _loan_to_revenue(inp, config),
        _credit_history(inp, config),
        _closed_credits(inp, config),
        _industry_risk(inp, config),
        _revenue_dynamics(inp, config),
        _mass_address(inp, config),
        _mass_director(inp, config),
        _credit_inquiries(inp, config),
    ]
    total = sum(f.points for f in factors)
    risk_class = _classify(total, config)
    return ScoringResult(
        factors=factors,
        total_score=total,
        max_score=config.max_score,
        risk_class=risk_class,
    )


def _classify(score: int, config: ScoringConfig) -> RiskClass:
    if score >= config.class_a_min:
        return RiskClass.A
    if score >= config.class_b_min:
        return RiskClass.B
    if score >= config.class_c_min:
        return RiskClass.C
    return RiskClass.D


def _business_age(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    months = inp.business_age_months
    if months >= cfg.age_mid_months:
        points = cfg.age_mature_points
    elif months >= cfg.age_young_months:
        points = cfg.age_mid_points
    else:
        points = cfg.age_young_points
    if months >= cfg.age_mid_months:
        explanation = "Бизнесу больше 3 лет"
    elif months >= cfg.age_young_months:
        explanation = "Бизнесу от 1 до 3 лет"
    else:
        explanation = "Бизнесу меньше 1 года"

    return FactorResult(
        name="business_age",
        points=points,
        max_points=cfg.age_mature_points,
        value=f"{months} мес.",
        description="Возраст бизнеса",
        explanation=explanation,
    )


def _debt_load(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    new_payment = calculate_annuity_payment(
        inp.requested_amount,
        inp.min_annual_rate,
        inp.requested_term_months,
    )
    total_payments = inp.existing_monthly_payments + new_payment

    if inp.monthly_revenue <= 0:
        ratio = Decimal("1")
    else:
        ratio = total_payments / inp.monthly_revenue

    pct = round(float(ratio) * 100, 1)

    if ratio < cfg.debt_low_pct:
        points = cfg.debt_low_points
        explanation = "Низкая долговая нагрузка"
    elif ratio <= cfg.debt_high_pct:
        points = cfg.debt_mid_points
        explanation = "Умеренная долговая нагрузка"
    else:
        points = cfg.debt_high_points
        explanation = "Высокая долговая нагрузка"

    return FactorResult(
        name="debt_load",
        points=points,
        max_points=cfg.debt_low_points,
        value=f"{pct}%",
        description="Долговая нагрузка",
        explanation=explanation,
    )


def _loan_to_revenue(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    annual_revenue = inp.monthly_revenue * 12

    if annual_revenue <= 0:
        ratio = Decimal("1")
    else:
        ratio = inp.requested_amount / annual_revenue

    pct = round(float(ratio) * 100, 1)

    if ratio < cfg.ltr_low_pct:
        points = cfg.ltr_low_points
        explanation = "Сумма заявки значительно меньше годовой выручки"
    elif ratio <= cfg.ltr_high_pct:
        points = cfg.ltr_mid_points
        explanation = "Сумма заявки соразмерна годовой выручке"
    else:
        points = cfg.ltr_high_points
        explanation = "Сумма заявки превышает половину годовой выручки"

    return FactorResult(
        name="loan_to_revenue",
        points=points,
        max_points=cfg.ltr_low_points,
        value=f"{pct}%",
        description="Сумма заявки / годовая выручка",
        explanation=explanation,
    )


def _credit_history(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    status = inp.credit_history_status
    points_map = {
        CreditHistoryStatus.CLEAN: cfg.credit_clean_points,
        CreditHistoryStatus.NO_HISTORY: cfg.credit_no_history_points,
        CreditHistoryStatus.MINOR_OVERDUE: cfg.credit_minor_overdue_points,
        CreditHistoryStatus.MAJOR_OVERDUE: cfg.credit_major_overdue_points,
    }
    labels = {
        CreditHistoryStatus.CLEAN: "без просрочек за последний год",
        CreditHistoryStatus.NO_HISTORY: "нет кредитной истории",
        CreditHistoryStatus.MINOR_OVERDUE: "закрытые просрочки до 30 дней",
        CreditHistoryStatus.MAJOR_OVERDUE: "просрочки более 30 дней или текущая до 30 дней",
    }
    explanations = {
        CreditHistoryStatus.CLEAN: "Чистая кредитная история",
        CreditHistoryStatus.NO_HISTORY: "Кредитная история отсутствует",
        CreditHistoryStatus.MINOR_OVERDUE: "Были небольшие просрочки за последний год",
        CreditHistoryStatus.MAJOR_OVERDUE: "Были серьёзные просрочки за последний год",
    }
    return FactorResult(
        name="credit_history",
        points=points_map[status],
        max_points=cfg.credit_clean_points,
        value=labels[status],
        description="Кредитная история",
        explanation=explanations[status],
    )


def _closed_credits(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    has = inp.has_closed_credits_without_overdue
    return FactorResult(
        name="closed_credits",
        points=cfg.closed_credits_yes_points if has else cfg.closed_credits_no_points,
        max_points=cfg.closed_credits_yes_points,
        value="есть" if has else "нет",
        description="Закрытые кредиты без просрочек",
        explanation="Есть успешно закрытые кредиты" if has else "Нет закрытых кредитов",
    )


def _industry_risk(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    risk = get_industry_risk(inp.okved_code)
    points_map = {
        IndustryRisk.LOW: cfg.industry_low_points,
        IndustryRisk.MEDIUM: cfg.industry_mid_points,
        IndustryRisk.HIGH: cfg.industry_high_points,
    }
    labels = {
        IndustryRisk.LOW: "низкий",
        IndustryRisk.MEDIUM: "средний",
        IndustryRisk.HIGH: "высокий",
    }
    explanations = {
        IndustryRisk.LOW: "Отрасль с низким уровнем риска",
        IndustryRisk.MEDIUM: "Отрасль со средним уровнем риска",
        IndustryRisk.HIGH: "Отрасль с высоким уровнем риска",
    }
    return FactorResult(
        name="industry_risk",
        points=points_map[risk],
        max_points=cfg.industry_low_points,
        value=f"{labels[risk]} ({inp.okved_code})",
        description="Отраслевой риск",
        explanation=explanations[risk],
    )


def _revenue_dynamics(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    if inp.last_2q_revenue is None or inp.prev_2q_revenue is None:
        return FactorResult(
            name="revenue_dynamics",
            points=0,
            max_points=0,
            value="недостаточно данных",
            description="Динамика выручки",
            explanation="Недостаточно данных для оценки динамики",
        )

    if inp.prev_2q_revenue <= 0:
        return FactorResult(
            name="revenue_dynamics",
            points=0,
            max_points=0,
            value="нет базы для сравнения",
            description="Динамика выручки",
            explanation="Недостаточно данных для оценки динамики",
        )

    change = (inp.last_2q_revenue - inp.prev_2q_revenue) / inp.prev_2q_revenue
    drop = float(-change)
    pct = round(float(change) * 100, 1)

    if drop > float(cfg.revenue_drop_pct):
        points = cfg.revenue_drop_points
        explanation = "Выручка значительно снизилась"
    else:
        points = cfg.revenue_ok_points
        explanation = "Выручка стабильна или растёт"

    return FactorResult(
        name="revenue_dynamics",
        points=points,
        max_points=0,
        value=f"{pct:+}%",
        description="Динамика выручки",
        explanation=explanation,
    )


def _mass_address(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    hit = inp.is_mass_address
    return FactorResult(
        name="mass_address",
        points=cfg.mass_address_points if hit else cfg.no_mass_address_points,
        max_points=0,
        value="да" if hit else "нет",
        description="Адрес массовой регистрации",
        explanation="Адрес массовой регистрации" if hit else "Адрес не массовый",
    )


def _mass_director(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    hit = inp.is_mass_director_or_founder
    return FactorResult(
        name="mass_director",
        points=cfg.mass_director_points if hit else cfg.no_mass_director_points,
        max_points=0,
        value="да" if hit else "нет",
        description="Массовый руководитель или учредитель",
        explanation="Руководитель или учредитель является массовым" if hit else "Руководитель и учредитель не массовые",
    )


def _credit_inquiries(inp: ScoringInput, cfg: ScoringConfig) -> FactorResult:
    count = inp.credit_inquiries_3_months
    over = count > cfg.inquiries_threshold
    return FactorResult(
        name="credit_inquiries",
        points=cfg.inquiries_high_points if over else cfg.inquiries_ok_points,
        max_points=0,
        value=str(count),
        description="Обращения за кредитами за 3 месяца",
        explanation="Частые обращения за кредитами" if over else "Нет частых обращений за кредитами",
    )
