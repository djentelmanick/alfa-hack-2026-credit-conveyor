"""Дозапрос по периоду выписки и подлинность выписки (SCOPE раздел 4, этап 2, пункты 2 и 3)."""

from datetime import date

import pytest

from app.domain.checks import (
    BankSignatureStrategy,
    FnsRevenueReconciliationStrategy,
    StatementVerifier,
    default_authenticity_strategies,
)
from app.domain.enums import CheckOutcome, CompanyKind, SignatureStatus
from app.domain.value_objects import Money
from tests.fakes.context import make_company, make_context, make_statement
from tests.fakes.scenarios import TODAY

IE_INN = "770512345650"


@pytest.fixture
def verifier() -> StatementVerifier:
    return StatementVerifier(default_authenticity_strategies())


@pytest.mark.parametrize(
    ("date_from", "outcome"),
    [
        (date(2026, 4, 24), CheckOutcome.PASSED),  # 24.04–23.10: ровно 6 месяцев
        (date(2026, 4, 25), CheckOutcome.FAILED),  # на день короче
    ],
)
def test_period_needs_at_least_6_months(
    verifier: StatementVerifier, date_from: date, outcome: CheckOutcome
) -> None:
    statement = make_statement(date_from=date_from, date_to=date(2026, 10, 23))
    assert verifier.check_period(statement, TODAY).outcome is outcome


@pytest.mark.parametrize(
    ("last_operation", "outcome"),
    [
        (date(2026, 9, 24), CheckOutcome.PASSED),  # ровно месяц назад
        (date(2026, 9, 23), CheckOutcome.FAILED),  # старше месяца
    ],
)
def test_last_operation_must_be_within_a_month(
    verifier: StatementVerifier, last_operation: date, outcome: CheckOutcome
) -> None:
    statement = make_statement(
        date_from=date(2025, 10, 24), date_to=date(2026, 10, 23), last_operation=last_operation
    )
    assert verifier.check_period(statement, TODAY).outcome is outcome


@pytest.mark.parametrize(
    ("signature", "outcome"),
    [
        (SignatureStatus.VALID, CheckOutcome.PASSED),
        (SignatureStatus.INVALID, CheckOutcome.FAILED),
        (SignatureStatus.ABSENT, CheckOutcome.FAILED),
    ],
)
def test_bank_signature_strategy(signature: SignatureStatus, outcome: CheckOutcome) -> None:
    statement = make_statement(signature=signature)
    ctx = make_context(statement=statement)
    strategy = BankSignatureStrategy()
    assert strategy.supports(ctx.company)
    assert strategy.verify(statement, ctx).outcome is outcome


def test_revenue_reconciliation_only_for_llc() -> None:
    strategy = FnsRevenueReconciliationStrategy()
    assert strategy.supports(make_company())
    assert not strategy.supports(
        make_company(inn=IE_INN, kind=CompanyKind.IE, director_inn=IE_INN, founders=())
    )


@pytest.mark.parametrize(
    ("reported", "outcome"),
    [
        ("10000000", CheckOutcome.PASSED),  # выписка 12 млн: +20% ровно
        ("9999999", CheckOutcome.FAILED),  # чуть больше 20%
        ("15000000", CheckOutcome.PASSED),  # выписка на 20% меньше
        ("15000001", CheckOutcome.FAILED),
    ],
)
def test_revenue_reconciliation_allows_20_percent(reported: str, outcome: CheckOutcome) -> None:
    statement = make_statement(signature=SignatureStatus.ABSENT, monthly_incoming="1000000")
    ctx = make_context(statement=statement, reported_annual_revenue=Money.of(reported))
    assert statement.annual_revenue() == Money.of("12000000")
    assert FnsRevenueReconciliationStrategy().verify(statement, ctx).outcome is outcome


def test_revenue_reconciliation_without_reporting_has_no_data() -> None:
    statement = make_statement(signature=SignatureStatus.ABSENT)
    ctx = make_context(statement=statement, reported_annual_revenue=None)
    result = FnsRevenueReconciliationStrategy().verify(statement, ctx)
    assert result.outcome is CheckOutcome.NO_DATA


def test_statement_verified_by_any_strategy(verifier: StatementVerifier) -> None:
    statement = make_statement(signature=SignatureStatus.ABSENT)
    ctx = make_context(statement=statement, reported_annual_revenue=Money.of("12000000"))
    verification = verifier.verify(statement, ctx)
    assert verification.verified
    assert {c.code for c in verification.checks} == {"BANK_SIGNATURE", "FNS_REVENUE_MATCH"}


def test_statement_not_verified_when_all_strategies_fail(verifier: StatementVerifier) -> None:
    statement = make_statement(signature=SignatureStatus.ABSENT)
    ctx = make_context(statement=statement, reported_annual_revenue=Money.of("20000000"))
    assert not verifier.verify(statement, ctx).verified


def test_individual_entrepreneur_checked_by_signature_only(verifier: StatementVerifier) -> None:
    company = make_company(inn=IE_INN, kind=CompanyKind.IE, director_inn=IE_INN, founders=())
    statement = make_statement(inn=IE_INN, signature=SignatureStatus.VALID)
    ctx = make_context(company=company, statement=statement, turnover=None)
    verification = verifier.verify(statement, ctx)
    assert verification.verified
    assert [c.code for c in verification.checks] == ["BANK_SIGNATURE"]
