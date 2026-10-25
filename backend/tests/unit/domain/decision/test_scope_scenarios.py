"""Пять сценариев SCOPE раздел 12 на уровне домена.

Синтетика этапа 2, FixedScoringEngine и DecisionEngine.

Ставки и исходы взяты из SCOPE, подгонять их под код нельзя.
Порядок шагов повторяет конвейер (SCOPE раздел 4): стоп-факторы, дозапрос, скоринг, решение.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.checks import (
    PipelineContext,
    StatementVerifier,
    default_authenticity_strategies,
    default_manual_review_triggers,
    default_stop_rules,
)
from app.domain.decision import DecisionEngine, RatePolicy, default_decision_rules
from app.domain.entities import CheckResult, Decision
from app.domain.enums import CheckOutcome, CompanyKind, DecisionOutcome, Purpose, Variant
from app.domain.products import ProductCatalog
from app.domain.value_objects import Money
from app.infra.mocks import SyntheticData
from tests.fakes.context import checking_data, collect_context, make_application, make_collateral
from tests.fakes.scenarios import IVANOVA, KOLOS, ROMASHKA, SOKOLOV, TECHPROM
from tests.fakes.scoring import FixedScoringEngine

STATEMENTS_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "infra" / "mocks" / "data" / "statements"
)


@dataclass(frozen=True, slots=True)
class PathResult:
    stop_factor: CheckResult | None = None
    info_request: CheckResult | None = None
    decision: Decision | None = None


type Runner = Callable[[PipelineContext], PathResult]


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


@pytest.fixture
def scoring() -> FixedScoringEngine:
    return FixedScoringEngine()


@pytest.fixture
def run(catalog: ProductCatalog, scoring: FixedScoringEngine) -> Runner:
    engine = DecisionEngine(
        default_decision_rules(catalog, RatePolicy()), default_manual_review_triggers()
    )
    verifier = StatementVerifier(default_authenticity_strategies())

    def _run(ctx: PipelineContext) -> PathResult:
        for rule in default_stop_rules(catalog):
            result = rule.check(checking_data(ctx))
            if result.outcome is CheckOutcome.STOP_FACTOR:
                return PathResult(stop_factor=result)
        if ctx.statement is not None:
            period = verifier.check_period(ctx.statement, ctx.today)
            if not period.passed:
                return PathResult(info_request=period)
        return PathResult(decision=engine.decide(ctx, scoring.score(ctx)))

    return _run


async def test_scenario_1_kolos_approved_at_23_5(fixtures: SyntheticData, run: Runner) -> None:
    application = make_application(inn=KOLOS, amount="2000000", term_months=12)
    result = run(await collect_context(fixtures, application))

    assert result.decision is not None
    assert result.decision.outcome is DecisionOutcome.APPROVED
    assert result.decision.rate == Decimal("23.5")


async def test_scenario_2_romashka_rejected_by_stop_factor(
    fixtures: SyntheticData, run: Runner, scoring: FixedScoringEngine
) -> None:
    application = make_application(inn=ROMASHKA, amount="1000000", term_months=12)
    result = run(await collect_context(fixtures, application))

    assert result.stop_factor is not None
    assert result.stop_factor.code == "COMPANY_INACTIVE"
    assert result.decision is None
    assert scoring.calls == []


async def test_scenario_3_sokolov_counter_offer_at_26_5(
    fixtures: SyntheticData, run: Runner
) -> None:
    application = make_application(
        inn=SOKOLOV,
        kind=CompanyKind.IE,
        amount="4500000",
        term_months=24,
        owner_guarantee=False,
    )
    result = run(await collect_context(fixtures, application))

    decision = result.decision
    assert decision is not None
    assert decision.outcome is DecisionOutcome.COUNTER_OFFER
    assert decision.rate == Decimal("26.5")
    unsecured, secured = decision.offers
    assert (unsecured.variant, unsecured.amount, unsecured.rate) == (
        Variant.UNSECURED,
        Money.of("3600000"),
        Decimal("26.5"),
    )
    assert (secured.variant, secured.amount) == (Variant.SECURED, Money.of("4500000"))


async def test_scenario_4_ivanova_needs_info_then_approved_at_26_5(
    fixtures: SyntheticData, run: Runner, scoring: FixedScoringEngine
) -> None:
    application = make_application(
        inn=IVANOVA,
        kind=CompanyKind.IE,
        amount="3000000",
        term_months=24,
        owner_guarantee=False,
    )

    first = run(
        await collect_context(
            fixtures, application, statement_raw=(STATEMENTS_DIR / "ivanova_4m.txt").read_bytes()
        )
    )
    assert first.info_request is not None
    assert first.info_request.code == "STATEMENT_PERIOD"
    assert scoring.calls == []

    ctx = await collect_context(
        fixtures,
        application,
        statement_raw=(STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes(),
    )
    assert ctx.statement_verification is not None
    assert ctx.statement_verification.verified
    second = run(ctx)
    assert second.decision is not None
    assert second.decision.outcome is DecisionOutcome.APPROVED
    assert second.decision.rate == Decimal("26.5")


async def test_scenario_5_techprom_manual_review_analyst_rate_23(
    fixtures: SyntheticData, run: Runner, catalog: ProductCatalog
) -> None:
    application = make_application(
        inn=TECHPROM,
        purpose=Purpose.REGULAR_PURCHASES,
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        amount="12000000",
        term_months=60,
        owner_guarantee=False,
        collateral=make_collateral("9000000"),
    )
    result = run(await collect_context(fixtures, application))

    decision = result.decision
    assert decision is not None
    assert decision.outcome is DecisionOutcome.MANUAL_REVIEW
    assert set(decision.reasons) == {"SECURED_VARIANT", "AMOUNT_OVER_5M"}
    # Аналитик одобряет по 23%: ставка в диапазоне варианта и совпадает с подсказкой по классу A.
    terms = catalog.get("REVOLVING_LINE").variant(Variant.SECURED)
    RatePolicy().validate_manual(terms, Decimal(23))
    assert decision.rate == Decimal(23)
