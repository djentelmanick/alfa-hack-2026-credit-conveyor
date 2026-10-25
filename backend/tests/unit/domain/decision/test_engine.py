"""DecisionEngine: правила по порядку, первое сработавшее даёт решение (SCOPE раздел 6)."""

import pytest

from app.domain.checks import PipelineContext, default_manual_review_triggers
from app.domain.decision import (
    ClassAApproveRule,
    ClassBApproveRule,
    ClassCCounterOfferRule,
    ClassDRejectRule,
    DecisionEngine,
    ManualReviewRule,
    RatePolicy,
    RevenueLimitCounterOfferRule,
    default_decision_rules,
)
from app.domain.enums import DecisionOutcome, RiskClass, Variant
from app.domain.products import ProductCatalog
from app.domain.scoring import ScoringEngine, ScoringResult
from tests.fakes.context import make_application, make_collateral, make_context, make_turnover


def _scoring(risk_class: RiskClass) -> ScoringResult:
    return ScoringResult(total=50, risk_class=risk_class, debt_load=0.2)


def _over_revenue_limit() -> PipelineContext:
    """Выручка 12 млн (лимит без залога 3,6 млн), сумма 4 млн: выше лимита, но без повода 5 млн."""
    return make_context(
        application=make_application(amount="4000000"), turnover=make_turnover(monthly="1000000")
    )


@pytest.fixture
def engine(catalog: ProductCatalog) -> DecisionEngine:
    return DecisionEngine(
        default_decision_rules(catalog, RatePolicy(), ScoringEngine(catalog)),
        default_manual_review_triggers(),
    )


def test_default_rules_are_in_scope_order(catalog: ProductCatalog) -> None:
    rules = sorted(
        default_decision_rules(catalog, RatePolicy(), ScoringEngine(catalog)), key=lambda r: r.order
    )
    assert [type(r) for r in rules] == [
        ManualReviewRule,
        ClassDRejectRule,
        RevenueLimitCounterOfferRule,
        ClassAApproveRule,
        ClassBApproveRule,
        ClassCCounterOfferRule,
    ]
    assert [r.order for r in rules] == [1, 2, 3, 4, 5, 6]


def test_order_comes_from_rule_not_from_list_position(catalog: ProductCatalog) -> None:
    rules = default_decision_rules(catalog, RatePolicy(), ScoringEngine(catalog))
    shuffled = [rules[i] for i in (4, 2, 5, 0, 3, 1)]
    engine = DecisionEngine(shuffled, default_manual_review_triggers())
    ctx = make_context(application=make_application(amount="8000000"))
    # Сумма больше 5 млн: правило 1 обязано сработать раньше правил 3 и 4.
    assert engine.decide(ctx, _scoring(RiskClass.A)).outcome is DecisionOutcome.MANUAL_REVIEW


def test_rules_with_same_order_are_rejected(catalog: ProductCatalog) -> None:
    rates = RatePolicy()
    with pytest.raises(ValueError, match="order"):
        DecisionEngine([ClassAApproveRule(catalog, rates), ClassAApproveRule(catalog, rates)], [])


def test_manual_review_wins_over_class_d(engine: DecisionEngine) -> None:
    ctx = make_context(application=make_application(amount="5500000"))
    decision = engine.decide(ctx, _scoring(RiskClass.D))
    assert decision.outcome is DecisionOutcome.MANUAL_REVIEW
    assert decision.reasons == ("AMOUNT_OVER_5M",)


def test_class_d_wins_over_revenue_limit(engine: DecisionEngine) -> None:
    decision = engine.decide(_over_revenue_limit(), _scoring(RiskClass.D))
    assert decision.outcome is DecisionOutcome.REJECTED


@pytest.mark.parametrize("risk_class", [RiskClass.A, RiskClass.B])
def test_revenue_limit_wins_over_approve(engine: DecisionEngine, risk_class: RiskClass) -> None:
    decision = engine.decide(_over_revenue_limit(), _scoring(risk_class))
    assert decision.outcome is DecisionOutcome.COUNTER_OFFER
    assert decision.rule_code == RevenueLimitCounterOfferRule.code


def test_class_c_over_limit_gets_class_c_offer(engine: DecisionEngine) -> None:
    decision = engine.decide(_over_revenue_limit(), _scoring(RiskClass.C))
    assert decision.rule_code == ClassCCounterOfferRule.code


def test_engine_reports_reasons_in_trigger_order(engine: DecisionEngine) -> None:
    application = make_application(
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        amount="12000000",
        term_months=60,
        collateral=make_collateral("9000000"),
    )
    ctx = make_context(application=application)
    assert engine.manual_review_reasons(ctx, _scoring(RiskClass.A)) == [
        "SECURED_VARIANT",
        "AMOUNT_OVER_5M",
    ]
