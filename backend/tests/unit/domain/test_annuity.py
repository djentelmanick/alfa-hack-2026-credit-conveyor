from datetime import date
from decimal import Decimal

import pytest

from app.domain.products import AnnuityCalculator, LumpSumLoan, ProductCatalog
from app.domain.value_objects import Money

# Сценарий 1 из SCOPE, раздел 12: 2 млн ₽, 12 мес, 23,5%.
AMOUNT = Money.of("2000000")
TERM = 12
RATE = Decimal("23.5")
START = date(2026, 1, 31)


def test_payment_matches_annuity_formula() -> None:
    # Независимый расчёт той же формулы во float: P*r / (1 - (1+r)^-n), r = 23.5/1200.
    assert AnnuityCalculator.payment(AMOUNT, TERM, RATE) == Money.of("188635.58")


def test_zero_rate_splits_amount_evenly() -> None:
    assert AnnuityCalculator.payment(Money.of("1200000"), 12, Decimal(0)) == Money.of("100000")


@pytest.mark.parametrize(
    ("term", "rate", "message"),
    [(0, Decimal("23.5"), "Срок"), (12, Decimal("-1"), "Ставка")],
)
def test_invalid_terms_are_rejected(term: int, rate: Decimal, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AnnuityCalculator.payment(AMOUNT, term, rate)


class TestSchedule:
    @pytest.fixture
    def product(self, catalog: ProductCatalog) -> LumpSumLoan:
        product = catalog.get("LUMP_SUM")
        assert isinstance(product, LumpSumLoan)
        return product

    def test_has_one_item_per_month(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)

        assert len(schedule) == TERM

    def test_due_dates_are_monthly_and_clamped_to_month_end(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)

        assert [item.due_date for item in schedule[:3]] == [
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
        ]
        assert schedule[-1].due_date == date(2027, 1, 31)

    def test_principal_sums_to_amount_and_balance_reaches_zero(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)

        assert sum((item.principal for item in schedule), Money.zero()) == AMOUNT
        assert schedule[-1].balance == Money.zero()

    def test_payments_are_equal_except_last_rounding(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)
        payment = AnnuityCalculator.payment(AMOUNT, TERM, RATE)

        assert all(item.payment == payment for item in schedule[:-1])
        assert abs((schedule[-1].payment - payment).amount) <= Decimal("0.12")

    def test_first_interest_is_month_of_rate_on_full_amount(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)

        # 2 000 000 * 23,5% / 12 = 39 166,666…
        assert schedule[0].interest == Money.of("39166.67")
        assert schedule[0].principal == Money.of("188635.58") - Money.of("39166.67")
        assert schedule[0].balance == AMOUNT - schedule[0].principal

    def test_balance_decreases_by_principal_each_month(self, product: LumpSumLoan) -> None:
        schedule = product.build_schedule(AMOUNT, TERM, RATE, START)

        previous = AMOUNT
        for item in schedule:
            assert item.balance == previous - item.principal
            previous = item.balance
