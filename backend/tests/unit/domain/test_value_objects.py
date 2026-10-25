from datetime import date
from decimal import Decimal

import pytest

from app.domain.dates import add_months, full_months_between
from app.domain.enums import CollateralType
from app.domain.value_objects import Collateral, Money, Period


class TestMoney:
    def test_rounds_half_up_to_kopecks(self) -> None:
        assert Money.of("0.005") == Money.of("0.01")
        assert Money.of("2.344") == Money.of("2.34")
        assert Money(Decimal("-0.005")).amount == Decimal("-0.01")

    def test_str_has_two_decimals(self) -> None:
        assert str(Money.of(3600000)) == "3600000.00"

    def test_arithmetic(self) -> None:
        assert Money.of("10.10") + Money.of("0.95") == Money.of("11.05")
        assert Money.of("10.10") - Money.of("0.95") == Money.of("9.15")
        assert Money.of("100") * Decimal("0.235") == Money.of("23.50")
        assert Money.of("100") / 3 == Money.of("33.33")
        assert Money.of("12000000").percent(Decimal(30)) == Money.of("3600000")

    def test_comparison(self) -> None:
        assert Money.of("1") < Money.of("1.01")
        assert min(Money.of("5"), Money.of("3")) == Money.of("3")

    def test_float_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            Money(1.5)
        with pytest.raises(TypeError):
            Money.of(1.5)
        with pytest.raises(TypeError):
            Money.of("1") * 0.5

    def test_does_not_mix_with_plain_numbers(self) -> None:
        with pytest.raises(TypeError):
            Money.of("1") + Decimal("1")
        with pytest.raises(TypeError):
            _ = Money.of("1") < Decimal("2")

    def test_non_finite_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="конечным"):
            Money(Decimal("NaN"))


class TestCalendar:
    def test_add_months_clamps_to_month_end(self) -> None:
        assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
        assert add_months(date(2026, 1, 31), 2) == date(2026, 3, 31)
        assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
        assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)

    def test_full_months_between(self) -> None:
        assert full_months_between(date(2021, 4, 10), date(2026, 4, 10)) == 60
        assert full_months_between(date(2021, 4, 10), date(2026, 4, 9)) == 59
        assert full_months_between(date(2026, 1, 31), date(2026, 2, 28)) == 1


class TestPeriod:
    def test_calendar_year_is_twelve_months(self) -> None:
        assert Period(date(2025, 1, 1), date(2025, 12, 31)).months() == 12

    def test_counts_only_full_months(self) -> None:
        assert Period(date(2026, 1, 15), date(2026, 7, 14)).months() == 6
        assert Period(date(2026, 1, 15), date(2026, 7, 13)).months() == 5

    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Конец периода"):
            Period(date(2026, 2, 1), date(2026, 1, 31))


class TestCollateral:
    @pytest.mark.parametrize(
        ("value", "covers"),
        [("500000.00", True), ("499999.99", False), ("1000000", True)],
    )
    def test_covers_half_of_amount(self, value: str, covers: bool) -> None:
        collateral = Collateral(CollateralType.EQUIPMENT, "Станок", Money.of(value))

        assert collateral.covers(Money.of("1000000"), Decimal(50)) is covers
