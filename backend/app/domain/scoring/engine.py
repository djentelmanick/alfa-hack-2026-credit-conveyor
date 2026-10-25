"""Балльная модель скоринга: 10 факторов, максимум 100 баллов (SCOPE, раздел 5)."""

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.domain.enums import RiskClass
from app.domain.products import LoanProduct, ProductCatalog, VariantConfig
from app.domain.scoring.model import ScoringModel
from app.domain.scoring.result import FactorScore, ScoringResult
from app.domain.value_objects import CreditReport

if TYPE_CHECKING:
    from app.domain.checks.context import PipelineContext

# Пороги и баллы из SCOPE, раздел 5.
_CLASS_A_MIN = 70
_CLASS_B_MIN = 50
_CLASS_C_MIN = 30

_AGE_YOUNG = 12
_AGE_MID = 36

_DEBT_LOW = Decimal("0.20")
_DEBT_HIGH = Decimal("0.40")

_LTR_LOW = Decimal("0.25")
_LTR_HIGH = Decimal("0.50")

_REVENUE_DROP = Decimal("0.30")
_INQUIRIES_LIMIT = 3
_HISTORY_MIN_MONTHS = 12


class ScoringEngine(ScoringModel):
    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog
        self._risk_map = _build_risk_map(catalog.config.industry_risk)

    def score(self, ctx: PipelineContext) -> ScoringResult:
        product = self._catalog.get(ctx.application.product_code)
        variant = product.variant(ctx.application.variant)
        report = ctx.credit_report

        debt_ratio = _debt_ratio(ctx, product, variant)
        factors = (
            _business_age(ctx),
            _debt_load(debt_ratio),
            _loan_to_revenue(ctx),
            _credit_history(report),
            _closed_credits(report),
            _industry_risk(ctx.company.okved, self._risk_map),
            _revenue_dynamics(ctx),
            _mass_address(ctx),
            _mass_director(ctx),
            _credit_inquiries(report),
        )
        total = sum(f.points for f in factors)
        return ScoringResult(
            total=total,
            risk_class=_classify(total),
            debt_load=round(float(debt_ratio), 4),
            factors=factors,
        )


def _classify(total: int) -> RiskClass:
    if total >= _CLASS_A_MIN:
        return RiskClass.A
    if total >= _CLASS_B_MIN:
        return RiskClass.B
    if total >= _CLASS_C_MIN:
        return RiskClass.C
    return RiskClass.D


def _build_risk_map(cfg: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for level, prefixes in cfg.items():
        for prefix in prefixes:
            result[prefix] = level
    return result


def _okved_risk(okved: str, risk_map: Mapping[str, str]) -> str:
    for length in range(len(okved), 0, -1):
        if okved[:length] in risk_map:
            return risk_map[okved[:length]]
    return "medium"


def _debt_ratio(ctx: PipelineContext, product: LoanProduct, variant: VariantConfig) -> Decimal:
    monthly = ctx.monthly_revenue()
    if monthly.is_zero():
        return Decimal(1)
    new_payment = product.monthly_payment(
        ctx.application.amount, ctx.application.term_months, variant.rate_min
    )
    total = (ctx.credit_report.monthly_payments + new_payment).amount
    return total / monthly.amount


# ── Факторы ──────────────────────────────────────────────────────────


def _business_age(ctx: PipelineContext) -> FactorScore:
    months = ctx.company.age_months(ctx.today)
    if months >= _AGE_MID:
        points, expl = 20, "Бизнесу больше 3 лет"
    elif months >= _AGE_YOUNG:
        points, expl = 10, "Бизнесу от 1 до 3 лет"
    else:
        points, expl = 0, "Бизнесу меньше 1 года"
    return FactorScore(
        code="BUSINESS_AGE",
        label="Возраст бизнеса",
        value=f"{months} мес.",
        points=points,
        explanation=expl,
    )


def _debt_load(ratio: Decimal) -> FactorScore:
    pct = round(float(ratio) * 100, 1)
    if ratio < _DEBT_LOW:
        points, expl = 25, "Низкая долговая нагрузка"
    elif ratio <= _DEBT_HIGH:
        points, expl = 10, "Умеренная долговая нагрузка"
    else:
        points, expl = -15, "Высокая долговая нагрузка"
    return FactorScore(
        code="DEBT_LOAD",
        label="Долговая нагрузка",
        value=f"{pct}%",
        points=points,
        explanation=expl,
    )


def _loan_to_revenue(ctx: PipelineContext) -> FactorScore:
    annual = ctx.annual_revenue()
    ratio = Decimal(1) if annual.is_zero() else ctx.application.amount.amount / annual.amount
    pct = round(float(ratio) * 100, 1)
    if ratio < _LTR_LOW:
        points, expl = 15, "Сумма заявки значительно меньше годовой выручки"
    elif ratio <= _LTR_HIGH:
        points, expl = 5, "Сумма заявки соразмерна годовой выручке"
    else:
        points, expl = -10, "Сумма заявки превышает половину годовой выручки"
    return FactorScore(
        code="AMOUNT_TO_REVENUE",
        label="Сумма заявки / годовая выручка",
        value=f"{pct}%",
        points=points,
        explanation=expl,
    )


def _credit_history(r: CreditReport) -> FactorScore:
    if not r.has_history:
        return FactorScore(
            code="CREDIT_HISTORY",
            label="Кредитная история",
            value="нет кредитной истории",
            points=0,
            explanation="Кредитная история отсутствует",
        )
    if r.current_overdue_days > 0 or r.max_closed_overdue_days_last_year > 30:
        return FactorScore(
            code="CREDIT_HISTORY",
            label="Кредитная история",
            value="просрочки более 30 дней или текущая",
            points=-20,
            explanation="Были серьёзные просрочки за последний год",
        )
    if r.max_closed_overdue_days_last_year > 0:
        return FactorScore(
            code="CREDIT_HISTORY",
            label="Кредитная история",
            value="закрытые просрочки до 30 дней",
            points=-10,
            explanation="Были небольшие просрочки за последний год",
        )
    return FactorScore(
        code="CREDIT_HISTORY",
        label="Кредитная история",
        value="без просрочек за последний год",
        points=20,
        explanation="Чистая кредитная история",
    )


def _closed_credits(r: CreditReport) -> FactorScore:
    if r.has_closed_loans_without_overdue:
        return FactorScore(
            code="CLOSED_LOANS",
            label="Закрытые кредиты без просрочек",
            value="есть",
            points=10,
            explanation="Есть успешно закрытые кредиты",
        )
    return FactorScore(
        code="CLOSED_LOANS",
        label="Закрытые кредиты без просрочек",
        value="нет",
        points=0,
        explanation="Нет закрытых кредитов",
    )


def _industry_risk(okved: str, risk_map: Mapping[str, str]) -> FactorScore:
    level = _okved_risk(okved, risk_map)
    pts = {"low": 10, "medium": 0, "high": -10}
    labels = {"low": "низкий", "medium": "средний", "high": "высокий"}
    expls = {
        "low": "Отрасль с низким уровнем риска",
        "medium": "Отрасль со средним уровнем риска",
        "high": "Отрасль с высоким уровнем риска",
    }
    return FactorScore(
        code="INDUSTRY_RISK",
        label="Отраслевой риск",
        value=f"{labels[level]} ({okved})",
        points=pts[level],
        explanation=expls[level],
    )


def _revenue_dynamics(ctx: PipelineContext) -> FactorScore:
    turnover = ctx.turnover
    if turnover is None or len(turnover.months) < _HISTORY_MIN_MONTHS:
        return FactorScore(
            code="REVENUE_DYNAMICS",
            label="Динамика выручки",
            value="недостаточно данных",
            points=0,
            explanation="Недостаточно данных для оценки динамики",
        )

    months = turnover.months
    recent = sum((m.incoming.amount for m in months[-6:]), Decimal(0))
    previous = sum((m.incoming.amount for m in months[-12:-6]), Decimal(0))

    if previous <= 0:
        return FactorScore(
            code="REVENUE_DYNAMICS",
            label="Динамика выручки",
            value="нет базы для сравнения",
            points=0,
            explanation="Недостаточно данных для оценки динамики",
        )

    change = (recent - previous) / previous
    pct = round(float(change) * 100, 1)

    if -change > _REVENUE_DROP:
        return FactorScore(
            code="REVENUE_DYNAMICS",
            label="Динамика выручки",
            value=f"{pct:+}%",
            points=-15,
            explanation="Выручка значительно снизилась",
        )
    return FactorScore(
        code="REVENUE_DYNAMICS",
        label="Динамика выручки",
        value=f"{pct:+}%",
        points=0,
        explanation="Выручка стабильна или растёт",
    )


def _mass_address(ctx: PipelineContext) -> FactorScore:
    hit = ctx.tax.mass_address
    return FactorScore(
        code="MASS_ADDRESS",
        label="Адрес массовой регистрации",
        value="да" if hit else "нет",
        points=-10 if hit else 0,
        explanation="Адрес массовой регистрации" if hit else "Адрес не массовый",
        client_visible=False,
    )


def _mass_director(ctx: PipelineContext) -> FactorScore:
    hit = ctx.tax.mass_director or ctx.tax.mass_founder
    return FactorScore(
        code="MASS_DIRECTOR",
        label="Массовый руководитель или учредитель",
        value="да" if hit else "нет",
        points=-10 if hit else 0,
        explanation="Руководитель или учредитель является массовым"
        if hit
        else "Руководитель и учредители не массовые",
        client_visible=False,
    )


def _credit_inquiries(r: CreditReport) -> FactorScore:
    count = r.inquiries_last_3_months
    over = count > _INQUIRIES_LIMIT
    return FactorScore(
        code="CREDIT_INQUIRIES",
        label="Обращения за кредитами за 3 месяца",
        value=str(count),
        points=-10 if over else 0,
        explanation="Частые обращения за кредитами"
        if over
        else "Нет частых обращений за кредитами",
        client_visible=False,
    )
