from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities import CreditLineContract
from app.domain.enums import CheckOutcome, ContractStatus, OperationType, Variant
from app.domain.errors import NotFound
from app.domain.products import (
    AnnuityCalculator,
    LumpSumLoan,
    ProductCatalog,
    RevolvingCreditLine,
)
from app.domain.value_objects import Money


@pytest.fixture
def lump_sum(catalog: ProductCatalog) -> LumpSumLoan:
    product = catalog.get("LUMP_SUM")
    assert isinstance(product, LumpSumLoan)
    return product


@pytest.fixture
def line(catalog: ProductCatalog) -> RevolvingCreditLine:
    product = catalog.get("REVOLVING_LINE")
    assert isinstance(product, RevolvingCreditLine)
    return product


class TestRevenueLimit:
    # Сценарий 3 из SCOPE, раздел 12: выручка 12 млн/год, лимит без залога 3,6 млн, с залогом 6 млн.
    def test_unsecured_is_30_percent_of_revenue(self, lump_sum: LumpSumLoan) -> None:
        assert lump_sum.revenue_limit(Variant.UNSECURED, Money.of("12000000")) == Money.of(
            "3600000"
        )

    def test_secured_is_50_percent_of_revenue(self, lump_sum: LumpSumLoan) -> None:
        assert lump_sum.revenue_limit(Variant.SECURED, Money.of("12000000")) == Money.of("6000000")

    @pytest.mark.parametrize(
        ("variant", "cap"), [(Variant.UNSECURED, "30000000"), (Variant.SECURED, "150000000")]
    )
    def test_lump_sum_limit_is_capped_by_variant_maximum(
        self, lump_sum: LumpSumLoan, variant: Variant, cap: str
    ) -> None:
        assert lump_sum.revenue_limit(variant, Money.of("1000000000")) == Money.of(cap)

    def test_secured_line_is_capped_by_60_millions(self, line: RevolvingCreditLine) -> None:
        assert line.revenue_limit(Variant.SECURED, Money.of("200000000")) == Money.of("60000000")

    def test_secured_line_scenario_5(self, line: RevolvingCreditLine) -> None:
        # Сценарий 5: выручка 60 млн/год, лимит с залогом 30 млн, заявка на 12 млн укладывается.
        assert line.revenue_limit(Variant.SECURED, Money.of("60000000")) == Money.of("30000000")


class TestValidateTerms:
    @pytest.mark.parametrize(
        ("amount", "term", "outcomes"),
        [
            ("300000", 12, [CheckOutcome.PASSED, CheckOutcome.PASSED]),
            ("30000000", 60, [CheckOutcome.PASSED, CheckOutcome.PASSED]),
            ("299999.99", 12, [CheckOutcome.FAILED, CheckOutcome.PASSED]),
            ("30000000.01", 60, [CheckOutcome.FAILED, CheckOutcome.PASSED]),
            ("1000000", 11, [CheckOutcome.PASSED, CheckOutcome.FAILED]),
            ("1000000", 61, [CheckOutcome.PASSED, CheckOutcome.FAILED]),
        ],
    )
    def test_unsecured_lump_sum_bounds(
        self, lump_sum: LumpSumLoan, amount: str, term: int, outcomes: list[CheckOutcome]
    ) -> None:
        results = lump_sum.validate_terms(Variant.UNSECURED, Money.of(amount), term)

        assert {r.code for r in results} == {"TERMS_IN_RANGE"}
        assert [r.outcome for r in results] == outcomes

    def test_secured_lump_sum_allows_ten_years(self, lump_sum: LumpSumLoan) -> None:
        results = lump_sum.validate_terms(Variant.SECURED, Money.of("150000000"), 120)

        assert all(r.outcome is CheckOutcome.PASSED for r in results)

    @pytest.mark.parametrize(("term", "ok"), [(17, False), (18, True), (19, False)])
    def test_unsecured_line_term_is_fixed_18_months(
        self, line: RevolvingCreditLine, term: int, ok: bool
    ) -> None:
        results = line.validate_terms(Variant.UNSECURED, Money.of("1000000"), term)

        assert (results[1].outcome is CheckOutcome.PASSED) is ok


class TestMonthlyPayment:
    def test_lump_sum_payment_is_annuity(self, lump_sum: LumpSumLoan) -> None:
        assert lump_sum.monthly_payment(
            Money.of("2000000"), 12, Decimal("23.5")
        ) == AnnuityCalculator.payment(Money.of("2000000"), 12, Decimal("23.5"))

    def test_line_payment_is_annuity_over_line_term(self, line: RevolvingCreditLine) -> None:
        # SCOPE, раздел 5: для ВКЛ весь лимит считается выбранным и гасится за срок линии.
        assert line.monthly_payment(
            Money.of("12000000"), 60, Decimal("23")
        ) == AnnuityCalculator.payment(Money.of("12000000"), 60, Decimal("23"))


class TestAllowedOperations:
    def test_lump_sum_has_no_tranches(self, lump_sum: LumpSumLoan) -> None:
        operations = lump_sum.allowed_operations()

        assert OperationType.PARTIAL_PREPAYMENT in operations
        assert OperationType.TRANCHE_DRAWN not in operations

    def test_line_has_no_partial_prepayment(self, line: RevolvingCreditLine) -> None:
        operations = line.allowed_operations()

        assert OperationType.TRANCHE_DRAWN in operations
        assert OperationType.PARTIAL_PREPAYMENT not in operations


def _line_contract(
    variant: Variant,
    *,
    available: str = "12000000",
    term_months: int = 60,
    status: ContractStatus = ContractStatus.ACTIVE,
) -> CreditLineContract:
    return CreditLineContract(
        id=uuid4(),
        application_id=uuid4(),
        number="ВКЛ-2026-000001",
        product_code="REVOLVING_LINE",
        variant=variant,
        rate=Decimal("23"),
        term_months=term_months,
        start_date=date(2026, 1, 15),
        contract_date=date(2026, 1, 15),
        status=status,
        limit=Money.of("12000000"),
        available=Money.of(available),
    )


class TestCanDraw:
    TODAY = date(2026, 2, 1)

    def test_tranche_settings_come_from_config(self, line: RevolvingCreditLine) -> None:
        assert line.tranche_min == Money.of("50000")
        assert line.tranche_term_months == 6

    @pytest.mark.parametrize(("amount", "ok"), [("49999.99", False), ("50000", True)])
    def test_minimum_tranche_is_50_thousands(
        self, line: RevolvingCreditLine, amount: str, ok: bool
    ) -> None:
        contract = _line_contract(Variant.SECURED)

        assert line.can_draw(contract, Money.of(amount), self.TODAY) is ok

    @pytest.mark.parametrize(("amount", "ok"), [("700000", True), ("700000.01", False)])
    def test_tranche_cannot_exceed_available_limit(
        self, line: RevolvingCreditLine, amount: str, ok: bool
    ) -> None:
        contract = _line_contract(Variant.SECURED, available="700000")

        assert line.can_draw(contract, Money.of(amount), self.TODAY) is ok

    def test_closed_contract_cannot_draw(self, line: RevolvingCreditLine) -> None:
        contract = _line_contract(Variant.SECURED, status=ContractStatus.CLOSED)

        assert not line.can_draw(contract, Money.of("100000"), self.TODAY)

    def test_cannot_draw_before_contract_start(self, line: RevolvingCreditLine) -> None:
        contract = _line_contract(Variant.SECURED)

        assert not line.can_draw(contract, Money.of("100000"), date(2026, 1, 14))

    @pytest.mark.parametrize(
        ("today", "ok"),
        [(date(2030, 7, 14), True), (date(2030, 7, 15), False), (date(2031, 1, 1), False)],
    )
    def test_secured_line_stops_draws_6_months_before_end(
        self, line: RevolvingCreditLine, today: date, ok: bool
    ) -> None:
        # Линия с 2026-01-15 на 60 мес заканчивается 2031-01-15, транши можно брать до 2030-07-15.
        contract = _line_contract(Variant.SECURED)

        assert line.can_draw(contract, Money.of("100000"), today) is ok

    def test_unsecured_line_prolongs_and_keeps_drawing(self, line: RevolvingCreditLine) -> None:
        contract = _line_contract(Variant.UNSECURED, term_months=18)

        assert line.can_draw(contract, Money.of("100000"), date(2027, 7, 1))


def test_unknown_product_is_not_found(catalog: ProductCatalog) -> None:
    with pytest.raises(NotFound):
        catalog.get("OVERDRAFT")
