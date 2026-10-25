"""Стоп-факторы SCOPE раздел 4, этап 2: каждый на границе срабатывания."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.checks import (
    BusinessAgeUnder6mRule,
    CbrRedLevelRule,
    CompanyInactiveRule,
    CurrentOverdueOver30Rule,
    DirectorDisqualifiedRule,
    ForbiddenOkvedRule,
    PipelineContext,
    RosfinListRule,
    StopFactorRule,
    TaxBlockOrBailiffRule,
    default_stop_rules,
)
from app.domain.enums import CheckOutcome, CheckStage, CompanyKind, CompanyStatus, KycLevel
from app.domain.products import ProductCatalog
from app.domain.value_objects import BailiffInfo, Founder, Money
from tests.fakes.context import (
    COMPANY_INN,
    DIRECTOR_INN,
    FOUNDER_INN,
    checking_data,
    clean_report,
    clean_tax,
    make_company,
    make_context,
)
from tests.fakes.scenarios import TODAY


def _stopped(rule: StopFactorRule, ctx: PipelineContext) -> bool:
    result = rule.check(checking_data(ctx))
    assert result.code == rule.code
    assert result.stage is CheckStage.CHECKING
    assert result.outcome in (CheckOutcome.PASSED, CheckOutcome.STOP_FACTOR)
    return result.outcome is CheckOutcome.STOP_FACTOR


def test_default_rules_follow_scope_order(catalog: ProductCatalog) -> None:
    assert [type(rule) for rule in default_stop_rules(catalog)] == [
        CompanyInactiveRule,
        BusinessAgeUnder6mRule,
        CurrentOverdueOver30Rule,
        ForbiddenOkvedRule,
        TaxBlockOrBailiffRule,
        CbrRedLevelRule,
        RosfinListRule,
        DirectorDisqualifiedRule,
    ]


def test_healthy_company_passes_every_rule(catalog: ProductCatalog) -> None:
    ctx = make_context()
    assert not any(_stopped(rule, ctx) for rule in default_stop_rules(catalog))


@pytest.mark.parametrize(
    ("status", "stopped"),
    [
        (CompanyStatus.ACTIVE, False),
        (CompanyStatus.LIQUIDATED, True),
        (CompanyStatus.BANKRUPT, True),
        (CompanyStatus.REORGANIZING, True),
    ],
)
def test_company_inactive(status: CompanyStatus, stopped: bool) -> None:
    ctx = make_context(company=make_company(status=status))
    assert _stopped(CompanyInactiveRule(), ctx) is stopped


@pytest.mark.parametrize(
    ("registered_at", "stopped"),
    [
        (date(2026, 4, 24), False),  # ровно 6 месяцев
        (date(2026, 4, 25), True),  # 5 месяцев и 30 дней
    ],
)
def test_business_age_under_6_months(registered_at: date, stopped: bool) -> None:
    ctx = make_context(company=make_company(registered_at=registered_at))
    assert ctx.today == TODAY
    assert _stopped(BusinessAgeUnder6mRule(), ctx) is stopped


@pytest.mark.parametrize(("days", "stopped"), [(30, False), (31, True)])
def test_current_overdue_of_company(days: int, stopped: bool) -> None:
    ctx = make_context(credit_report=clean_report(COMPANY_INN, current_overdue_days=days))
    assert _stopped(CurrentOverdueOver30Rule(), ctx) is stopped


@pytest.mark.parametrize(("days", "stopped"), [(30, False), (31, True)])
def test_current_overdue_of_director(days: int, stopped: bool) -> None:
    ctx = make_context(
        related_credit_reports=(
            clean_report(DIRECTOR_INN, current_overdue_days=days),
            clean_report(FOUNDER_INN),
        )
    )
    assert _stopped(CurrentOverdueOver30Rule(), ctx) is stopped


@pytest.mark.parametrize(("share", "stopped"), [(Decimal("19.99"), False), (Decimal(20), True)])
def test_current_overdue_of_founder_counts_from_20_percent(share: Decimal, stopped: bool) -> None:
    company = make_company(
        founders=(
            Founder(full_name="Петров Пётр Петрович", inn=DIRECTOR_INN, share_pct=100 - share),
            Founder(full_name="Сидоров Сидор Сидорович", inn=FOUNDER_INN, share_pct=share),
        )
    )
    ctx = make_context(
        company=company,
        related_credit_reports=(
            clean_report(DIRECTOR_INN),
            clean_report(FOUNDER_INN, current_overdue_days=90),
        ),
    )
    assert _stopped(CurrentOverdueOver30Rule(), ctx) is stopped


def test_current_overdue_of_individual_entrepreneur() -> None:
    inn = "770498765403"
    company = make_company(inn=inn, kind=CompanyKind.IE, director_inn=inn, founders=())
    ctx = make_context(
        company=company,
        credit_report=clean_report(inn, current_overdue_days=31),
        related_credit_reports=(),
    )
    assert _stopped(CurrentOverdueOver30Rule(), ctx)


@pytest.mark.parametrize(
    ("okved", "stopped"),
    [
        ("92.11", True),  # азартные игры
        ("92", True),
        ("11.01", True),  # производство крепких напитков
        ("11.02.1", True),
        ("11.05", False),  # пиво не в списке запрещённых
        ("56.10", False),
        ("29.2", False),  # префикс «92» не должен совпадать с серединой кода
    ],
)
def test_forbidden_okved(catalog: ProductCatalog, okved: str, stopped: bool) -> None:
    rule = ForbiddenOkvedRule(catalog.config.forbidden_okved)
    ctx = make_context(company=make_company(okved=okved))
    assert _stopped(rule, ctx) is stopped


def test_tax_block_stops() -> None:
    ctx = make_context(tax=clean_tax(accounts_blocked=True))
    assert _stopped(TaxBlockOrBailiffRule(), ctx)


@pytest.mark.parametrize(("proceedings", "stopped"), [(0, False), (1, True)])
def test_company_bailiff_proceedings_stop_at_any_amount(proceedings: int, stopped: bool) -> None:
    debt = Money.of("1") if proceedings else Money.zero()
    ctx = make_context(
        bailiff=BailiffInfo(subject_inn=COMPANY_INN, open_proceedings=proceedings, total_debt=debt)
    )
    assert _stopped(TaxBlockOrBailiffRule(), ctx) is stopped


@pytest.mark.parametrize(
    ("level", "stopped"),
    [(KycLevel.GREEN, False), (KycLevel.YELLOW, False), (KycLevel.RED, True)],
)
def test_cbr_red_level(level: KycLevel, stopped: bool) -> None:
    ctx = make_context(cbr_level=level)
    assert _stopped(CbrRedLevelRule(), ctx) is stopped


@pytest.mark.parametrize("hit", [False, True])
def test_rosfin_list(hit: bool) -> None:
    ctx = make_context(rosfin_hit=hit)
    assert _stopped(RosfinListRule(), ctx) is hit


@pytest.mark.parametrize("disqualified", [False, True])
def test_director_disqualified(disqualified: bool) -> None:
    ctx = make_context(tax=clean_tax(director_disqualified=disqualified))
    assert _stopped(DirectorDisqualifiedRule(), ctx) is disqualified


def test_rule_explains_what_was_found() -> None:
    ctx = make_context(company=replace(make_company(), status=CompanyStatus.LIQUIDATED))
    result = CompanyInactiveRule().check(checking_data(ctx))
    assert result.expected
    assert result.actual
