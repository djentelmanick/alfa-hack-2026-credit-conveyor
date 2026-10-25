"""Тесты скоринга: 10 факторов, классы A–D, edge cases."""

from datetime import date

import pytest

from app.domain.dates import add_months
from app.domain.enums import RiskClass
from app.domain.products import ProductCatalog
from app.domain.scoring import FactorScore, ScoringEngine, ScoringResult
from app.domain.value_objects import Money, MonthlyTurnover, MonthRevenue
from tests.fakes.context import (
    COMPANY_INN,
    clean_report,
    clean_tax,
    make_application,
    make_company,
    make_context,
    make_turnover,
)


@pytest.fixture
def engine(catalog: ProductCatalog) -> ScoringEngine:
    return ScoringEngine(catalog)


# ── Здоровая компания: все факторы на максимуме → класс A ────────────


class TestHealthyCompany:
    def test_class_a(self, engine: ScoringEngine) -> None:
        ctx = make_context()
        result = engine.score(ctx)
        assert result.risk_class is RiskClass.A
        assert result.total >= 70

    def test_ten_factors(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        assert len(result.factors) == 10

    def test_factor_codes(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        codes = [f.code for f in result.factors]
        assert codes == [
            "BUSINESS_AGE",
            "DEBT_LOAD",
            "AMOUNT_TO_REVENUE",
            "CREDIT_HISTORY",
            "CLOSED_LOANS",
            "INDUSTRY_RISK",
            "REVENUE_DYNAMICS",
            "MASS_ADDRESS",
            "MASS_DIRECTOR",
            "CREDIT_INQUIRIES",
        ]

    def test_debt_load_ratio(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        assert 0 < result.debt_load < 1

    def test_antifraud_factors_not_visible(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        hidden = [f for f in result.factors if not f.client_visible]
        codes = {f.code for f in hidden}
        assert codes == {"MASS_ADDRESS", "MASS_DIRECTOR", "CREDIT_INQUIRIES"}


# ── Возраст бизнеса ──────────────────────────────────────────────────


class TestBusinessAge:
    def test_mature(self, engine: ScoringEngine) -> None:
        company = make_company(registered_at=date(2020, 1, 1))
        result = engine.score(make_context(company=company))
        assert _factor(result, "BUSINESS_AGE").points == 20

    def test_mid(self, engine: ScoringEngine) -> None:
        company = make_company(registered_at=date(2025, 6, 1))
        result = engine.score(make_context(company=company))
        assert _factor(result, "BUSINESS_AGE").points == 10

    def test_young(self, engine: ScoringEngine) -> None:
        company = make_company(registered_at=date(2026, 5, 1))
        result = engine.score(make_context(company=company))
        assert _factor(result, "BUSINESS_AGE").points == 0


# ── Долговая нагрузка ────────────────────────────────────────────────


class TestDebtLoad:
    def test_low(self, engine: ScoringEngine) -> None:
        # Нет существующих платежей, большая выручка → <20%
        result = engine.score(make_context())
        assert _factor(result, "DEBT_LOAD").points == 25

    def test_medium(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, monthly_payments=Money.of("300000"))
        ctx = make_context(
            application=make_application(amount="2000000", term_months=12),
            credit_report=report,
            turnover=make_turnover(monthly="1500000"),
        )
        result = engine.score(ctx)
        assert _factor(result, "DEBT_LOAD").points == 10

    def test_high(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, monthly_payments=Money.of("500000"))
        ctx = make_context(
            application=make_application(amount="5000000", term_months=12),
            credit_report=report,
            turnover=make_turnover(monthly="500000"),
        )
        result = engine.score(ctx)
        assert _factor(result, "DEBT_LOAD").points == -15

    def test_exactly_20_pct_is_not_low(
        self, engine: ScoringEngine, catalog: ProductCatalog
    ) -> None:
        application = make_application(amount="2000000", term_months=12)
        product = catalog.get(application.product_code)
        new_payment = product.monthly_payment(
            application.amount,
            application.term_months,
            product.variant(application.variant).rate_min,
        )
        report = clean_report(COMPANY_INN, monthly_payments=Money.of("400000") - new_payment)
        ctx = make_context(
            application=application, credit_report=report, turnover=make_turnover(monthly="2000000")
        )
        result = engine.score(ctx)
        assert result.debt_load == 0.2
        assert _factor(result, "DEBT_LOAD").points == 10


# ── Сумма заявки / годовая выручка ───────────────────────────────────


class TestLoanToRevenue:
    def test_low(self, engine: ScoringEngine) -> None:
        # amount 2M / annual 24M = 8.3% → +15
        result = engine.score(make_context())
        assert _factor(result, "AMOUNT_TO_REVENUE").points == 15

    def test_mid(self, engine: ScoringEngine) -> None:
        # amount 4M / annual 12M = 33% → +5
        ctx = make_context(
            application=make_application(amount="4000000", term_months=36),
            turnover=make_turnover(monthly="1000000"),
        )
        result = engine.score(ctx)
        assert _factor(result, "AMOUNT_TO_REVENUE").points == 5

    def test_high(self, engine: ScoringEngine) -> None:
        # amount 8M / annual 12M = 67% → -10
        ctx = make_context(
            application=make_application(amount="8000000", term_months=60),
            turnover=make_turnover(monthly="1000000"),
        )
        result = engine.score(ctx)
        assert _factor(result, "AMOUNT_TO_REVENUE").points == -10


# ── Кредитная история ────────────────────────────────────────────────


class TestCreditHistory:
    def test_clean(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        assert _factor(result, "CREDIT_HISTORY").points == 20

    def test_no_history(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, has_history=False)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CREDIT_HISTORY").points == 0

    def test_minor_overdue(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, max_closed_overdue_days_last_year=15)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CREDIT_HISTORY").points == -10

    def test_major_overdue(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, max_closed_overdue_days_last_year=60)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CREDIT_HISTORY").points == -20

    def test_current_overdue(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, current_overdue_days=5)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CREDIT_HISTORY").points == -20


# ── Закрытые кредиты ─────────────────────────────────────────────────


class TestClosedCredits:
    def test_has(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        assert _factor(result, "CLOSED_LOANS").points == 10

    def test_none(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, has_closed_loans_without_overdue=False)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CLOSED_LOANS").points == 0


# ── Отраслевой риск ──────────────────────────────────────────────────


class TestIndustryRisk:
    def test_low_risk(self, engine: ScoringEngine) -> None:
        # 62.01 — IT, low risk
        company = make_company(okved="62.01")
        result = engine.score(make_context(company=company))
        assert _factor(result, "INDUSTRY_RISK").points == 10

    def test_medium_risk(self, engine: ScoringEngine) -> None:
        # 56.10 — общепит, medium risk
        result = engine.score(make_context())
        assert _factor(result, "INDUSTRY_RISK").points == 0

    def test_high_risk(self, engine: ScoringEngine) -> None:
        # 41.20 — строительство, high risk
        company = make_company(okved="41.20")
        result = engine.score(make_context(company=company))
        assert _factor(result, "INDUSTRY_RISK").points == -10

    def test_unknown_okved_defaults_to_medium(self, engine: ScoringEngine) -> None:
        company = make_company(okved="99.99")
        result = engine.score(make_context(company=company))
        assert _factor(result, "INDUSTRY_RISK").points == 0


# ── Динамика выручки ─────────────────────────────────────────────────


class TestRevenueDynamics:
    def test_stable(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        assert _factor(result, "REVENUE_DYNAMICS").points == 0

    def test_no_data(self, engine: ScoringEngine) -> None:
        ctx = make_context(turnover=make_turnover(months=6))
        result = engine.score(ctx)
        assert _factor(result, "REVENUE_DYNAMICS").points == 0

    @pytest.mark.parametrize(
        ("previous", "recent", "points"),
        [
            ("2000000", "1000000", -15),  # падение на 50%
            ("2000000", "1400000", 0),  # ровно 30%: штраф только при падении больше 30%
            ("2000000", "1399999", -15),
            ("1000000", "2000000", 0),  # рост
        ],
    )
    def test_drop_threshold(
        self, engine: ScoringEngine, previous: str, recent: str, points: int
    ) -> None:
        turnover = _two_halves(previous, recent)
        result = engine.score(make_context(turnover=turnover))
        assert _factor(result, "REVENUE_DYNAMICS").points == points


# ── Антифрод-факторы ─────────────────────────────────────────────────


class TestAntifraudFactors:
    def test_mass_address(self, engine: ScoringEngine) -> None:
        tax = clean_tax(mass_address=True)
        result = engine.score(make_context(tax=tax))
        f = _factor(result, "MASS_ADDRESS")
        assert f.points == -10
        assert f.client_visible is False

    def test_mass_director(self, engine: ScoringEngine) -> None:
        tax = clean_tax(mass_director=True)
        result = engine.score(make_context(tax=tax))
        f = _factor(result, "MASS_DIRECTOR")
        assert f.points == -10
        assert f.client_visible is False

    def test_mass_founder(self, engine: ScoringEngine) -> None:
        tax = clean_tax(mass_founder=True)
        result = engine.score(make_context(tax=tax))
        f = _factor(result, "MASS_DIRECTOR")
        assert f.points == -10

    def test_high_inquiries(self, engine: ScoringEngine) -> None:
        report = clean_report(COMPANY_INN, inquiries_last_3_months=5)
        result = engine.score(make_context(credit_report=report))
        f = _factor(result, "CREDIT_INQUIRIES")
        assert f.points == -10
        assert f.client_visible is False

    @pytest.mark.parametrize(("count", "points"), [(2, 0), (3, 0), (4, -10)])
    def test_inquiries_boundary(self, engine: ScoringEngine, count: int, points: int) -> None:
        report = clean_report(COMPANY_INN, inquiries_last_3_months=count)
        result = engine.score(make_context(credit_report=report))
        assert _factor(result, "CREDIT_INQUIRIES").points == points


# ── Классы риска ─────────────────────────────────────────────────────


class TestRiskClasses:
    def test_class_d(self, engine: ScoringEngine) -> None:
        company = make_company(okved="41.20", registered_at=date(2026, 8, 1))
        report = clean_report(
            COMPANY_INN,
            has_history=False,
            has_closed_loans_without_overdue=False,
            monthly_payments=Money.of("500000"),
            inquiries_last_3_months=5,
        )
        tax = clean_tax(mass_address=True, mass_director=True)
        ctx = make_context(
            company=company,
            application=make_application(amount="5000000", term_months=12),
            credit_report=report,
            turnover=make_turnover(monthly="500000"),
            tax=tax,
        )
        result = engine.score(ctx)
        assert result.risk_class is RiskClass.D
        assert result.total < 30

    def test_class_c(self, engine: ScoringEngine) -> None:
        report = clean_report(
            COMPANY_INN,
            has_history=False,
            has_closed_loans_without_overdue=False,
            monthly_payments=Money.of("100000"),
        )
        ctx = make_context(
            application=make_application(amount="4000000", term_months=36),
            credit_report=report,
            turnover=make_turnover(monthly="1000000"),
        )
        result = engine.score(ctx)
        assert result.risk_class is RiskClass.C
        assert 30 <= result.total < 50

    def test_max_score_is_100(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context(company=make_company(okved="62.01")))
        assert result.total == 100
        assert result.risk_class is RiskClass.A

    def test_zero_revenue_gives_worst_revenue_factors(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context(turnover=make_turnover(monthly="0")))
        assert _factor(result, "DEBT_LOAD").points == -15
        assert _factor(result, "AMOUNT_TO_REVENUE").points == -10
        assert _factor(result, "REVENUE_DYNAMICS").points == 0


# ── Каждый фактор имеет explanation ──────────────────────────────────


class TestExplanations:
    def test_all_factors_have_explanation(self, engine: ScoringEngine) -> None:
        result = engine.score(make_context())
        for factor in result.factors:
            assert factor.explanation, f"Фактор {factor.code} без объяснения"


# ── Хелпер ───────────────────────────────────────────────────────────


def _factor(result: ScoringResult, code: str) -> FactorScore:
    return next(f for f in result.factors if f.code == code)


def _two_halves(previous: str, recent: str) -> MonthlyTurnover:
    first = date(2025, 10, 1)
    return MonthlyTurnover(
        inn=COMPANY_INN,
        months=tuple(
            MonthRevenue(
                month=add_months(first, i), incoming=Money.of(previous if i < 6 else recent)
            )
            for i in range(12)
        ),
    )
