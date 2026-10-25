"""Синтетика даёт ровно те входные данные, что в SCOPE, раздел 12.

Скоринг здесь не считается: проверяются только исходные величины и корзины, в которые они попадают.
"""

from decimal import Decimal

import pytest

from app.domain.entities import Consent
from app.domain.enums import CompanyStatus, KycLevel
from app.domain.products.annuity import AnnuityCalculator
from app.domain.value_objects import Money
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockRosfinAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from app.infra.parsers.statement_1c import Statement1CParser
from tests.unit.infra.conftest import ALL_INNS, ROMASHKA, SCENARIOS, TODAY, Scenario


async def _annual_revenue(
    scenario: Scenario, fixtures: SyntheticData, failures: MockFailures, statement_12m: bytes
) -> Money:
    turnover = await MockTurnoverAdapter(fixtures, failures).monthly_turnover(scenario.inn, 12)
    if turnover is None:
        return Statement1CParser().parse(statement_12m).annual_revenue()
    assert len(turnover.months) == 12
    return turnover.total()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.inn)
async def test_registry_data_matches_scope(
    scenario: Scenario, fixtures: SyntheticData, failures: MockFailures
) -> None:
    profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(scenario.inn)

    assert profile.kind is scenario.kind
    assert profile.status is CompanyStatus.ACTIVE
    assert profile.okved == scenario.okved
    age = profile.age_months(TODAY)
    if scenario.age_years is None:
        # ИП Иванова: «1,5 года», корзина возраста 12–36 месяцев.
        assert age == 18
    else:
        assert age // 12 == scenario.age_years
        assert age > 36


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.inn)
async def test_revenue_payments_and_history_match_scope(
    scenario: Scenario,
    fixtures: SyntheticData,
    failures: MockFailures,
    consent: Consent,
    statement_12m: bytes,
) -> None:
    revenue = await _annual_revenue(scenario, fixtures, failures, statement_12m)
    report = await MockBkiAdapter(fixtures, failures).report(scenario.inn, consent)

    assert revenue == Money(scenario.annual_revenue or Decimal(15_000_000))
    assert report.monthly_payments == Money(scenario.monthly_payments)
    assert report.has_history is scenario.has_history
    assert report.current_overdue_days == 0
    assert report.max_closed_overdue_days_last_year == 0
    assert report.has_closed_loans_without_overdue is scenario.has_closed_loans
    assert report.inquiries_last_3_months <= 3


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.inn)
async def test_debt_load_lands_in_scope_bucket(
    scenario: Scenario,
    fixtures: SyntheticData,
    failures: MockFailures,
    consent: Consent,
    statement_12m: bytes,
) -> None:
    revenue = await _annual_revenue(scenario, fixtures, failures, statement_12m)
    report = await MockBkiAdapter(fixtures, failures).report(scenario.inn, consent)
    new_payment = AnnuityCalculator.payment(
        Money(scenario.amount), scenario.term_months, scenario.min_rate
    )

    load_pct = (report.monthly_payments + new_payment).amount / (revenue.amount / 12) * 100

    assert scenario.approx_load_pct is not None
    # SCOPE даёт нагрузку «примерно», поэтому сверяем с точностью до процентного пункта.
    assert abs(load_pct - scenario.approx_load_pct) <= 1
    expected_bucket = "<20" if scenario.approx_load_pct < 20 else "20-40"
    actual_bucket = "<20" if load_pct < 20 else "20-40" if load_pct <= 40 else ">40"
    assert actual_bucket == expected_bucket


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.inn)
async def test_penalty_factors_do_not_fire(
    scenario: Scenario,
    fixtures: SyntheticData,
    failures: MockFailures,
    statement_12m: bytes,
) -> None:
    profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(scenario.inn)
    fns = MockFnsAdapter(fixtures, failures)
    tax = await fns.tax_info(scenario.inn)

    assert not tax.mass_address
    assert not tax.mass_director
    assert not tax.mass_founder
    assert not await fns.is_mass_address(profile.address)

    turnover = await MockTurnoverAdapter(fixtures, failures).monthly_turnover(scenario.inn, 12)
    if turnover is None:
        turnover = Statement1CParser().parse(statement_12m).monthly_turnover()
    months = [m.incoming.amount for m in turnover.months]
    last_half, first_half = sum(months[-6:]), sum(months[-12:-6])
    assert last_half >= first_half * Decimal("0.7")


@pytest.mark.parametrize("inn", ALL_INNS)
async def test_only_romashka_has_a_stop_factor(
    inn: str, fixtures: SyntheticData, failures: MockFailures, consent: Consent
) -> None:
    profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(inn)
    fns = MockFnsAdapter(fixtures, failures)
    bki = MockBkiAdapter(fixtures, failures)
    fssp = MockFsspAdapter(fixtures, failures)
    tax = await fns.tax_info(inn)
    persons = [profile.director, *(f.full_name for f in profile.founders)]

    expected_status = CompanyStatus.LIQUIDATED if inn == ROMASHKA else CompanyStatus.ACTIVE
    assert profile.status is expected_status
    assert profile.age_months(TODAY) >= 6
    assert not profile.okved.startswith(("92", "11.01", "11.02", "11.03", "11.04"))
    subjects = {inn, profile.director_inn} | {f.inn for f in profile.founders if f.share_pct >= 20}
    for subject in subjects:
        assert (await bki.report(subject, consent)).current_overdue_days == 0
    assert not await fns.account_blocks(inn)
    assert (await fssp.open_proceedings(inn)).open_proceedings == 0
    assert await MockCbrAdapter(fixtures, failures).risk_level(inn) is KycLevel.GREEN
    assert not await MockRosfinAdapter(fixtures, failures).is_listed(inn, persons)
    assert not tax.director_disqualified


@pytest.mark.parametrize("inn", ALL_INNS)
async def test_no_manual_review_triggers_from_sources(
    inn: str, fixtures: SyntheticData, failures: MockFailures
) -> None:
    profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(inn)
    tax = await MockFnsAdapter(fixtures, failures).tax_info(inn)
    director_fssp = await MockFsspAdapter(fixtures, failures).open_proceedings(profile.director_inn)

    assert profile.unreliable_records == ()
    if profile.director_changed_at is not None:
        assert (TODAY - profile.director_changed_at).days > 183
    assert tax.tax_debt <= Money.of(10_000)
    assert director_fssp.total_debt <= Money.of(10_000)
