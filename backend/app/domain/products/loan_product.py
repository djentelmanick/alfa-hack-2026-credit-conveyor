from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import ClassVar

from app.domain.dates import add_months
from app.domain.entities.contract import CreditLineContract, ScheduleItem
from app.domain.entities.parts import CheckResult
from app.domain.enums import CheckOutcome, CheckStage, OperationType, Variant
from app.domain.errors import FieldError, ValidationFailed
from app.domain.products.annuity import AnnuityCalculator
from app.domain.products.config import ProductConfig, VariantConfig
from app.domain.value_objects import Money


def _check(ok: bool, expected: str, actual: str) -> CheckResult:
    return CheckResult(
        code="TERMS_IN_RANGE",
        stage=CheckStage.FORM,
        expected=expected,
        actual=actual,
        outcome=CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
    )


class LoanProduct(ABC):
    def __init__(self, code: str, config: ProductConfig) -> None:
        self.code = code
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    def variant(self, variant: Variant) -> VariantConfig:
        try:
            return self.config.variants[variant]
        except KeyError:
            raise ValidationFailed(
                [FieldError("variant", "VARIANT_NOT_OFFERED", f"У продукта нет варианта {variant}")]
            ) from None

    def validate_terms(
        self, variant: Variant, amount: Money, term_months: int
    ) -> list[CheckResult]:
        """Две проверки TERMS_IN_RANGE: сумма и срок по границам варианта."""
        terms = self.variant(variant)
        return [
            _check(
                terms.min_amount <= amount <= terms.max_amount,
                f"сумма от {terms.min_amount} до {terms.max_amount}",
                str(amount),
            ),
            _check(
                terms.term_min <= term_months <= terms.term_max,
                f"срок от {terms.term_min} до {terms.term_max} мес",
                f"{term_months} мес",
            ),
        ]

    def revenue_limit(self, variant: Variant, annual_revenue: Money) -> Money:
        terms = self.variant(variant)
        return min(annual_revenue.percent(terms.revenue_limit_pct), terms.max_amount)

    @abstractmethod
    def monthly_payment(self, amount: Money, term_months: int, annual_rate_pct: Decimal) -> Money:
        """Платёж для расчёта долговой нагрузки в скоринге."""

    @abstractmethod
    def allowed_operations(self) -> frozenset[OperationType]: ...


class LumpSumLoan(LoanProduct):
    _OPERATIONS: ClassVar[frozenset[OperationType]] = frozenset(
        {
            OperationType.DISBURSEMENT,
            OperationType.SCHEDULED_PAYMENT,
            OperationType.PARTIAL_PREPAYMENT,
            OperationType.FULL_REPAYMENT,
            OperationType.CONTRACT_EDITED,
        }
    )

    def monthly_payment(self, amount: Money, term_months: int, annual_rate_pct: Decimal) -> Money:
        return AnnuityCalculator.payment(amount, term_months, annual_rate_pct)

    def build_schedule(
        self, amount: Money, term_months: int, annual_rate_pct: Decimal, start: date
    ) -> list[ScheduleItem]:
        """Первый платёж через месяц после start."""
        return AnnuityCalculator.schedule(amount, term_months, annual_rate_pct, start)

    def allowed_operations(self) -> frozenset[OperationType]:
        return self._OPERATIONS


class RevolvingCreditLine(LoanProduct):
    _OPERATIONS: ClassVar[frozenset[OperationType]] = frozenset(
        {
            OperationType.LIMIT_OPENED,
            OperationType.TRANCHE_DRAWN,
            OperationType.INTEREST_ACCRUED,
            OperationType.TRANCHE_REPAID,
            OperationType.FULL_REPAYMENT,
            OperationType.CONTRACT_EDITED,
        }
    )

    def __init__(self, code: str, config: ProductConfig) -> None:
        super().__init__(code, config)
        if config.tranche_min is None or config.tranche_term_months is None:
            raise ValueError(f"У продукта {code} не заданы параметры траншей")
        self.tranche_min = Money(config.tranche_min)
        self.tranche_term_months = config.tranche_term_months

    def monthly_payment(self, amount: Money, term_months: int, annual_rate_pct: Decimal) -> Money:
        # SCOPE, раздел 5: для нагрузки весь лимит считается выбранным
        # и гасится аннуитетом за срок линии.
        return AnnuityCalculator.payment(amount, term_months, annual_rate_pct)

    def allowed_operations(self) -> frozenset[OperationType]:
        return self._OPERATIONS

    def can_draw(self, contract: CreditLineContract, amount: Money, today: date) -> bool:
        if not contract.is_active() or today < contract.start_date:
            return False
        if amount < self.tranche_min or amount > contract.available:
            return False
        stop_months = self.variant(contract.variant).draw_stop_months_before_end
        if stop_months is not None:
            return today < add_months(contract.end_date, -stop_months)
        return True
