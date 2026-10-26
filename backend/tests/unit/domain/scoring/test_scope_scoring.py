"""Настоящая модель на синтетике сценариев SCOPE раздел 12 даёт эталонные баллы и класс."""

from pathlib import Path

import pytest

from app.domain.checks import PipelineContext
from app.domain.enums import CompanyKind, Purpose, Variant
from app.domain.products import ProductCatalog
from app.domain.scoring import ScoringEngine
from app.infra.mocks import SyntheticData
from tests.fakes.context import collect_context, make_application, make_collateral
from tests.fakes.scenarios import BYSTRY_GRUZ, IVANOVA, KOLOS, SOKOLOV, TECHPROM
from tests.fakes.scoring import SCOPE_RESULTS

STATEMENTS_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "infra" / "mocks" / "data" / "statements"
)


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


async def _kolos(fixtures: SyntheticData) -> PipelineContext:
    return await collect_context(
        fixtures, make_application(inn=KOLOS, amount="2000000", term_months=12)
    )


async def _sokolov(fixtures: SyntheticData) -> PipelineContext:
    application = make_application(
        inn=SOKOLOV, kind=CompanyKind.IE, amount="4500000", term_months=24, owner_guarantee=False
    )
    return await collect_context(fixtures, application)


async def _ivanova(fixtures: SyntheticData) -> PipelineContext:
    application = make_application(
        inn=IVANOVA, kind=CompanyKind.IE, amount="3000000", term_months=24, owner_guarantee=False
    )
    statement = (STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes()
    return await collect_context(fixtures, application, statement_raw=statement)


async def _techprom(fixtures: SyntheticData) -> PipelineContext:
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
    return await collect_context(fixtures, application)


async def _bystry_gruz(fixtures: SyntheticData) -> PipelineContext:
    return await collect_context(
        fixtures, make_application(inn=BYSTRY_GRUZ, amount="1500000", term_months=24)
    )


SCENARIOS = {
    KOLOS: _kolos,
    SOKOLOV: _sokolov,
    IVANOVA: _ivanova,
    TECHPROM: _techprom,
    BYSTRY_GRUZ: _bystry_gruz,
}


@pytest.mark.parametrize("inn", list(SCENARIOS))
async def test_scope_scenario_score(
    inn: str, fixtures: SyntheticData, catalog: ProductCatalog
) -> None:
    ctx = await SCENARIOS[inn](fixtures)
    result = ScoringEngine(catalog).score(ctx)
    expected = SCOPE_RESULTS[inn]

    assert result.total == expected.total
    assert result.risk_class is expected.risk_class
    assert result.debt_load == pytest.approx(expected.debt_load, abs=0.01)
    points = {f.code: f.points for f in result.factors}
    assert {f.code: f.points for f in expected.factors} == {
        code: points[code] for code in (f.code for f in expected.factors)
    }
    # Факторы вне SCOPE раздела 12 у сценариев не срабатывают.
    assert all(
        points[f.code] == 0
        for f in result.factors
        if f.code not in {e.code for e in expected.factors}
    )
