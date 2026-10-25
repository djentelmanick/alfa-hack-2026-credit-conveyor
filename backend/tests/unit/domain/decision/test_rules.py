"""Правила кредитного решения SCOPE раздел 6, каждое отдельно."""

from decimal import Decimal

import pytest

from app.domain.checks import PipelineContext
from app.domain.decision import (
    ClassAApproveRule,
    ClassBApproveRule,
    ClassCCounterOfferRule,
    ClassDRejectRule,
    ManualReviewRule,
    RatePolicy,
    RevenueLimitCounterOfferRule,
)
from app.domain.enums import DecisionOutcome, RiskClass, Variant
from app.domain.products import ProductCatalog
from app.domain.scoring import FactorScore, ScoringResult
from app.domain.value_objects import Money
from tests.fakes.context import make_application, make_context, make_turnover
from tests.fakes.scenarios import NOW


def _scoring(risk_class: RiskClass, *factors: FactorScore) -> ScoringResult:
    total = {RiskClass.A: 80, RiskClass.B: 60, RiskClass.C: 40, RiskClass.D: 10}[risk_class]
    return ScoringResult(total=total, risk_class=risk_class, debt_load=0.2, factors=factors)


def _ctx(
    amount: str = "2000000", term_months: int = 12, monthly_revenue: str = "2000000"
) -> PipelineContext:
    return make_context(
        application=make_application(amount=amount, term_months=term_months),
        turnover=make_turnover(monthly=monthly_revenue),
    )


@pytest.fixture
def rates() -> RatePolicy:
    return RatePolicy()


# --- 1. Ручная проверка ---


def test_manual_review_matches_only_with_reasons(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    rule = ManualReviewRule(catalog, rates)
    assert not rule.matches(_ctx(), _scoring(RiskClass.A), [])
    assert rule.matches(_ctx(), _scoring(RiskClass.A), ["AMOUNT_OVER_5M"])


def test_manual_review_keeps_reasons_and_suggests_class_rate(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    decision = ManualReviewRule(catalog, rates).apply(
        _ctx(), _scoring(RiskClass.B), ["SECURED_VARIANT", "AMOUNT_OVER_5M"]
    )
    assert decision.outcome is DecisionOutcome.MANUAL_REVIEW
    assert decision.rule_code == ManualReviewRule.code
    assert decision.reasons == ("SECURED_VARIANT", "AMOUNT_OVER_5M")
    assert decision.rate == Decimal("26.5")
    assert decision.decided_by is None
    assert decision.decided_at == NOW


def test_manual_review_for_class_d_has_no_suggested_rate(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    decision = ManualReviewRule(catalog, rates).apply(_ctx(), _scoring(RiskClass.D), ["X"])
    assert decision.rate is None


# --- 2. Класс D ---


@pytest.mark.parametrize(
    ("risk_class", "matches"),
    [(RiskClass.A, False), (RiskClass.B, False), (RiskClass.C, False), (RiskClass.D, True)],
)
def test_class_d_reject_matches(
    catalog: ProductCatalog, rates: RatePolicy, risk_class: RiskClass, matches: bool
) -> None:
    assert ClassDRejectRule(catalog, rates).matches(_ctx(), _scoring(risk_class), []) is matches


def test_class_d_reject_explains_worst_factors(catalog: ProductCatalog, rates: RatePolicy) -> None:
    scoring = _scoring(
        RiskClass.D,
        FactorScore(code="BUSINESS_AGE", label="Возраст", value="8 мес", points=0),
        FactorScore(code="CREDIT_HISTORY", label="КИ", value="просрочки", points=-10),
        FactorScore(code="DEBT_LOAD", label="Нагрузка", value="55%", points=-15),
    )
    decision = ClassDRejectRule(catalog, rates).apply(_ctx(), scoring, [])
    assert decision.outcome is DecisionOutcome.REJECTED
    assert decision.rate is None
    assert decision.reasons == ("DEBT_LOAD", "CREDIT_HISTORY")


# --- 3. Лимит от выручки ---


@pytest.mark.parametrize(
    ("amount", "risk_class", "matches"),
    [
        ("7200000", RiskClass.A, False),  # выручка 24 млн, лимит без залога 30% = 7,2 млн
        ("7200000.01", RiskClass.A, True),
        ("7200000.01", RiskClass.B, True),
        ("7200000.01", RiskClass.C, False),
    ],
)
def test_revenue_limit_matches(
    catalog: ProductCatalog, rates: RatePolicy, amount: str, risk_class: RiskClass, matches: bool
) -> None:
    rule = RevenueLimitCounterOfferRule(catalog, rates)
    assert rule.matches(_ctx(amount=amount), _scoring(risk_class), []) is matches


def test_revenue_limit_offers_unsecured_within_limit_and_secured_full_amount(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    ctx = _ctx(amount="4500000", term_months=24, monthly_revenue="1000000")
    decision = RevenueLimitCounterOfferRule(catalog, rates).apply(ctx, _scoring(RiskClass.B), [])
    assert decision.outcome is DecisionOutcome.COUNTER_OFFER
    assert decision.rate == Decimal("26.5")
    assert [(o.variant, o.amount, o.term_months, o.rate) for o in decision.offers] == [
        (Variant.UNSECURED, Money.of("3600000"), 24, Decimal("26.5")),
        (Variant.SECURED, Money.of("4500000"), 24, Decimal("24.5")),
    ]
    assert len({o.id for o in decision.offers}) == 2


def test_revenue_limit_skips_secured_option_above_secured_limit(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    ctx = _ctx(amount="7000000", term_months=24, monthly_revenue="1000000")
    decision = RevenueLimitCounterOfferRule(catalog, rates).apply(ctx, _scoring(RiskClass.A), [])
    assert [(o.variant, o.amount) for o in decision.offers] == [
        (Variant.UNSECURED, Money.of("3600000")),
    ]


def test_revenue_limit_without_any_option_goes_to_analyst(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    # Выручка 0,9 млн: лимит без залога 270 тыс. ниже минимума 300 тыс.,
    # а 400 тыс. с залогом ниже минимальной суммы залогового варианта.
    ctx = _ctx(amount="400000", term_months=12, monthly_revenue="75000")
    decision = RevenueLimitCounterOfferRule(catalog, rates).apply(ctx, _scoring(RiskClass.A), [])
    assert decision.outcome is DecisionOutcome.MANUAL_REVIEW
    assert decision.reasons == ("NO_COUNTER_OFFER_AVAILABLE",)


# --- 4 и 5. Классы A и B ---


@pytest.mark.parametrize(
    ("rule_cls", "risk_class", "rate"),
    [(ClassAApproveRule, RiskClass.A, "23.5"), (ClassBApproveRule, RiskClass.B, "26.5")],
)
def test_class_approve(
    catalog: ProductCatalog,
    rates: RatePolicy,
    rule_cls: type[ClassAApproveRule | ClassBApproveRule],
    risk_class: RiskClass,
    rate: str,
) -> None:
    rule = rule_cls(catalog, rates)
    for other in RiskClass:
        assert rule.matches(_ctx(), _scoring(other), []) is (other is risk_class)
    decision = rule.apply(_ctx(), _scoring(risk_class), [])
    assert decision.outcome is DecisionOutcome.APPROVED
    assert decision.rate == Decimal(rate)
    assert decision.offers == ()


# --- 6. Класс C ---


def test_class_c_matches_only_class_c(catalog: ProductCatalog, rates: RatePolicy) -> None:
    rule = ClassCCounterOfferRule(catalog, rates)
    for other in RiskClass:
        assert rule.matches(_ctx(), _scoring(other), []) is (other is RiskClass.C)


def test_class_c_offers_longer_term_and_collateral(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    decision = ClassCCounterOfferRule(catalog, rates).apply(_ctx(), _scoring(RiskClass.C), [])
    assert decision.outcome is DecisionOutcome.COUNTER_OFFER
    assert decision.rate == Decimal("29.5")
    assert [(o.variant, o.amount, o.term_months, o.rate) for o in decision.offers] == [
        (Variant.UNSECURED, Money.of("2000000"), 60, Decimal("29.5")),
        (Variant.SECURED, Money.of("2000000"), 12, Decimal("27.5")),
    ]


def test_class_c_at_max_term_offers_only_collateral(
    catalog: ProductCatalog, rates: RatePolicy
) -> None:
    decision = ClassCCounterOfferRule(catalog, rates).apply(
        _ctx(term_months=60), _scoring(RiskClass.C), []
    )
    assert [o.variant for o in decision.offers] == [Variant.SECURED]
