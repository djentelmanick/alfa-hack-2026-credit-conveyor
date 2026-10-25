"""Операции по договору и сдвиг его даты (SCOPE, раздел 8; BACKEND_SPEC, раздел 15)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.dates import add_months
from app.domain.entities import CreditLineContract, LoanContract
from app.domain.enums import ContractStatus, OperationType, Variant
from app.domain.errors import InvalidStatus, ValidationFailed
from app.domain.products import LumpSumLoan, ProductCatalog, RevolvingCreditLine
from app.domain.value_objects import Money

START = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CLIENT = uuid4()


def _lump_sum(catalog: ProductCatalog) -> LumpSumLoan:
    product = catalog.get("LUMP_SUM")
    assert isinstance(product, LumpSumLoan)
    return product


def _line_product(catalog: ProductCatalog) -> RevolvingCreditLine:
    product = catalog.get("REVOLVING_LINE")
    assert isinstance(product, RevolvingCreditLine)
    return product


def _loan(catalog: ProductCatalog, start: date = START) -> LoanContract:
    """Сценарий 1: 2 000 000 на 12 мес под 23,5%."""
    amount = Money.of(2_000_000)
    contract = LoanContract(
        id=uuid4(),
        application_id=uuid4(),
        number="КД-2026-000001",
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        rate=Decimal("23.5"),
        term_months=12,
        start_date=start,
        contract_date=start,
        principal=amount,
        outstanding=amount,
    )
    contract.add_version(
        document_key="c/v1.pdf",
        reason="Договор подписан",
        schedule=_lump_sum(catalog).build_schedule(amount, 12, Decimal("23.5"), start),
        author_id=CLIENT,
        now=NOW,
    )
    contract.record(OperationType.DISBURSEMENT, amount, actor_id=None)
    return contract


def _line() -> CreditLineContract:
    """Сценарий 5: лимит 12 000 000 на 60 мес под 23%, с залогом."""
    limit = Money.of(12_000_000)
    contract = CreditLineContract(
        id=uuid4(),
        application_id=uuid4(),
        number="КД-2026-000002",
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        rate=Decimal("23.0"),
        term_months=60,
        start_date=START,
        contract_date=START,
        limit=limit,
        available=limit,
    )
    contract.add_version(
        document_key="l/v1.pdf", reason="Договор подписан", schedule=(), author_id=CLIENT, now=NOW
    )
    contract.record(OperationType.LIMIT_OPENED, limit, actor_id=None)
    return contract


def _types(contract: LoanContract | CreditLineContract) -> list[OperationType]:
    return [op.type for op in contract.operations]


def _prepay(catalog: ProductCatalog, contract: LoanContract, amount: Money) -> None:
    product = _lump_sum(catalog)
    contract.prepay(
        amount,
        rebuild=lambda c: product.reschedule(c, c.rate, c.term_months),
        document_key=f"c/v{contract.next_version_no}.pdf",
        author_id=CLIENT,
        now=NOW,
    )


# --- Кредит одной суммой ---


def test_three_months_give_three_scheduled_payments(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)

    for _ in range(3):
        contract.advance_month()

    assert _types(contract).count(OperationType.SCHEDULED_PAYMENT) == 3
    assert contract.contract_date == add_months(START, 3)
    assert contract.outstanding == contract.current_version.schedule[2].balance
    assert all(op.emulated for op in contract.operations[1:])


def test_partial_prepayment_lowers_payment_keeps_term(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)
    for _ in range(3):
        contract.advance_month()
    old_payment = contract.current_version.schedule[3].payment
    debt = contract.outstanding

    _prepay(catalog, contract, Money.of(500_000))

    version = contract.current_version
    assert version.version_no == 2
    assert len(version.schedule) == 9
    assert version.schedule[-1].due_date == add_months(START, 12)
    assert version.schedule[0].payment < old_payment
    assert contract.outstanding == debt - Money.of(500_000)
    assert version.changes[0].field == "outstanding"
    assert contract.operations[-1].type is OperationType.PARTIAL_PREPAYMENT
    assert contract.operations[-1].actor_id == CLIENT


def test_prepayment_not_less_than_debt_is_rejected(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)

    with pytest.raises(ValidationFailed) as exc:
        _prepay(catalog, contract, contract.outstanding)

    assert exc.value.details[0].code == "AMOUNT_NOT_LESS_THAN_DEBT"
    assert len(contract.versions) == 1


def test_last_payment_closes_contract(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)

    for _ in range(12):
        contract.advance_month()

    assert contract.outstanding == Money.zero()
    assert contract.status is ContractStatus.CLOSED
    with pytest.raises(InvalidStatus):
        contract.advance_month()


def test_last_payment_after_prepayment_closes_contract(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)
    for _ in range(3):
        contract.advance_month()
    _prepay(catalog, contract, Money.of("333333.33"))

    contract.move_date(add_months(START, 12))

    assert contract.outstanding == Money.zero()
    assert contract.status is ContractStatus.CLOSED


def test_month_shift_from_31st(catalog: ProductCatalog) -> None:
    contract = _loan(catalog, start=date(2026, 1, 31))

    contract.advance_month()
    assert contract.contract_date == date(2026, 2, 28)
    contract.advance_month()
    assert contract.contract_date == date(2026, 3, 31)


def test_move_date_between_payments_pays_only_due_items(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)

    contract.move_date(date(2027, 1, 10))

    assert _types(contract).count(OperationType.SCHEDULED_PAYMENT) == 2
    assert contract.contract_date == date(2027, 1, 10)
    contract.advance_month()
    assert contract.contract_date == date(2027, 1, 24)
    assert _types(contract).count(OperationType.SCHEDULED_PAYMENT) == 3


def test_full_repayment_closes_loan(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)
    contract.advance_month()
    debt = contract.outstanding

    operation = contract.repay_full(CLIENT)

    assert operation.type is OperationType.FULL_REPAYMENT
    assert operation.amount == debt
    assert contract.outstanding == Money.zero()
    assert contract.status is ContractStatus.CLOSED


# --- ВКЛ ---


def test_tranche_bounds(catalog: ProductCatalog) -> None:
    product = _line_product(catalog)
    contract = _line()
    draw_until = product.draw_until(contract)

    assert [e.code for e in product.draw_errors(contract, Money.of("49999.99"), START)] == [
        "TRANCHE_BELOW_MIN"
    ]
    assert product.draw_errors(contract, Money.of(50_000), START) == []
    assert [e.code for e in product.draw_errors(contract, Money.of(12_000_001), START)] == [
        "OVER_AVAILABLE"
    ]
    assert [e.code for e in product.draw_errors(contract, Money.of(50_000), draw_until)] == [
        "DRAW_WINDOW_CLOSED"
    ]


def test_repaying_tranche_restores_available() -> None:
    contract = _line()
    first = contract.draw(Money.of(1_000_000), due_at=add_months(START, 6), actor_id=CLIENT)
    contract.draw(Money.of(2_000_000), due_at=add_months(START, 6), actor_id=CLIENT)
    assert contract.available == Money.of(9_000_000)

    contract.repay_tranche(first.id, Money.of(1_000_000), actor_id=CLIENT)

    assert contract.available == Money.of(10_000_000)
    assert contract.debt() == Money.of(2_000_000)
    assert contract.operations[-1].type is OperationType.TRANCHE_REPAID
    assert contract.operations[-1].tranche_id == first.id


def test_tranche_repayment_over_debt_is_rejected() -> None:
    contract = _line()
    tranche = contract.draw(Money.of(100_000), due_at=add_months(START, 6), actor_id=CLIENT)

    with pytest.raises(ValidationFailed) as exc:
        contract.repay_tranche(tranche.id, Money.of("100000.01"), actor_id=CLIENT)

    assert exc.value.details[0].code == "OVER_TRANCHE_DEBT"


def test_tranche_auto_repaid_at_due() -> None:
    contract = _line()
    tranche = contract.draw(Money.of(1_000_000), due_at=add_months(START, 6), actor_id=CLIENT)

    for _ in range(6):
        contract.advance_month()

    assert _types(contract).count(OperationType.INTEREST_ACCRUED) == 6
    repaid = [op for op in contract.operations if op.type is OperationType.TRANCHE_REPAID]
    assert len(repaid) == 1
    assert repaid[0].emulated
    assert repaid[0].tranche_id == tranche.id
    assert contract.available == Money.of(12_000_000)
    interest = next(op for op in contract.operations if op.type is OperationType.INTEREST_ACCRUED)
    assert interest.amount == Money.of("19166.67")


def test_full_repayment_closes_line() -> None:
    contract = _line()
    contract.draw(Money.of(1_000_000), due_at=add_months(START, 6), actor_id=CLIENT)

    contract.repay_full(CLIENT)

    assert contract.debt() == Money.zero()
    assert contract.available == contract.limit
    assert contract.status is ContractStatus.CLOSED


# --- Сброс ---


def test_reset_restores_issued_state(catalog: ProductCatalog) -> None:
    contract = _loan(catalog)
    product = _lump_sum(catalog)
    for _ in range(2):
        contract.advance_month()
    contract.edit_terms(
        rate=Decimal("25.0"),
        term_months=18,
        schedule=product.reschedule(contract, Decimal("25.0"), 18),
        document_key="c/v2.pdf",
        reason="Условия изменены аналитиком",
        author_id=uuid4(),
        now=NOW,
    )
    _prepay(catalog, contract, Money.of(100_000))

    removed = contract.reset_to_issued()

    assert removed == ["c/v2.pdf", "c/v3.pdf"]
    assert [v.version_no for v in contract.versions] == [1]
    assert _types(contract) == [OperationType.DISBURSEMENT]
    assert contract.rate == Decimal("23.5")
    assert contract.term_months == 12
    assert contract.outstanding == Money.of(2_000_000)
    assert contract.contract_date == START
    assert contract.status is ContractStatus.ACTIVE


def test_reset_reopens_closed_line() -> None:
    contract = _line()
    contract.draw(Money.of(1_000_000), due_at=add_months(START, 6), actor_id=CLIENT)
    contract.repay_full(CLIENT)

    assert contract.reset_to_issued() == []

    assert contract.tranches == []
    assert contract.available == contract.limit
    assert contract.status is ContractStatus.ACTIVE
    assert _types(contract) == [OperationType.LIMIT_OPENED]


def test_prepayment_schedule_keeps_dates_from_31st(catalog: ProductCatalog) -> None:
    contract = _loan(catalog, start=date(2026, 1, 31))
    contract.advance_month()

    _prepay(catalog, contract, Money.of(500_000))

    dates = [item.due_date for item in contract.current_version.schedule]
    assert dates[0] == date(2026, 3, 31)
    assert dates[1] == date(2026, 4, 30)
    assert dates[-1] == contract.end_date


def test_tranche_due_between_anniversaries_is_repaid_on_its_date() -> None:
    contract = _line()
    contract.move_date(date(2026, 11, 10))
    contract.draw(Money.of(1_000_000), due_at=date(2027, 5, 10), actor_id=CLIENT)

    contract.move_date(date(2027, 6, 24))

    repaid = [op for op in contract.operations if op.type is OperationType.TRANCHE_REPAID]
    assert [op.contract_date for op in repaid] == [date(2027, 5, 10)]
    assert _types(contract).count(OperationType.INTEREST_ACCRUED) == 6
