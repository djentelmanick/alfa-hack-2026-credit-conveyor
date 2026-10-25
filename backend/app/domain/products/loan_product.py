from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from app.domain.dates import add_months, full_months_between
from app.domain.entities.contract import (
    Contract,
    CreditLineContract,
    LoanContract,
    ScheduleItem,
)
from app.domain.entities.parts import CheckResult
from app.domain.enums import CheckOutcome, CheckStage, OperationType, Variant
from app.domain.errors import FieldError, ValidationFailed
from app.domain.products.annuity import AnnuityCalculator
from app.domain.products.config import ProductConfig, VariantConfig
from app.domain.value_objects import Money

if TYPE_CHECKING:
    from app.domain.entities.application import Application

SIGNED_REASON = "Договор подписан"


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractOpening:
    """Всё, что для договора выбирает сервис подписания: продукт сам этого не знает."""

    contract_id: UUID
    number: str
    rate: Decimal
    document_key: str
    signer_id: UUID
    now: datetime
    today: date


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

    @abstractmethod
    def open_contract(self, app: Application, opening: ContractOpening) -> Contract:
        """Договор с версией 1 и операцией выдачи. Дата начала это день подписания."""

    @abstractmethod
    def reschedule(self, contract: Contract, rate: Decimal, term_months: int) -> list[ScheduleItem]:
        """График новой версии договора после правки ставки или срока."""


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
        self,
        amount: Money,
        term_months: int,
        annual_rate_pct: Decimal,
        start: date,
        *,
        skip_months: int = 0,
    ) -> list[ScheduleItem]:
        """Первый платёж через skip_months + 1 месяцев после start."""
        return AnnuityCalculator.schedule(
            amount, term_months, annual_rate_pct, start, skip_months=skip_months
        )

    def allowed_operations(self) -> frozenset[OperationType]:
        return self._OPERATIONS

    def open_contract(self, app: Application, opening: ContractOpening) -> LoanContract:
        contract = LoanContract(
            id=opening.contract_id,
            application_id=app.id,
            number=opening.number,
            product_code=self.code,
            variant=app.variant,
            rate=opening.rate,
            term_months=app.term_months,
            start_date=opening.today,
            contract_date=opening.today,
            principal=app.amount,
            outstanding=app.amount,
        )
        contract.add_version(
            document_key=opening.document_key,
            reason=SIGNED_REASON,
            schedule=self.build_schedule(app.amount, app.term_months, opening.rate, opening.today),
            author_id=opening.signer_id,
            now=opening.now,
        )
        contract.record(OperationType.DISBURSEMENT, app.amount, actor_id=None)
        return contract

    def reschedule(self, contract: Contract, rate: Decimal, term_months: int) -> list[ScheduleItem]:
        """Остаток долга гасится за оставшиеся месяцы, даты платежей остаются прежними."""
        elapsed = full_months_between(contract.start_date, contract.contract_date)
        remaining = term_months - elapsed
        if remaining <= 0:
            raise ValidationFailed(
                [
                    FieldError(
                        "term_months",
                        "TERM_TOO_SHORT",
                        f"Прошло уже {elapsed} мес, срок должен быть больше",
                    )
                ]
            )
        return self.build_schedule(
            contract.debt(), remaining, rate, contract.start_date, skip_months=elapsed
        )


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

    def open_contract(self, app: Application, opening: ContractOpening) -> CreditLineContract:
        contract = CreditLineContract(
            id=opening.contract_id,
            application_id=app.id,
            number=opening.number,
            product_code=self.code,
            variant=app.variant,
            rate=opening.rate,
            term_months=app.term_months,
            start_date=opening.today,
            contract_date=opening.today,
            limit=app.amount,
            available=app.amount,
        )
        # У линии нет аннуитетного графика: проценты и долг считаются по каждому траншу.
        contract.add_version(
            document_key=opening.document_key,
            reason=SIGNED_REASON,
            schedule=(),
            author_id=opening.signer_id,
            now=opening.now,
        )
        contract.record(OperationType.LIMIT_OPENED, app.amount, actor_id=None)
        return contract

    def reschedule(self, contract: Contract, rate: Decimal, term_months: int) -> list[ScheduleItem]:
        return []

    def draw_until(self, contract: CreditLineContract) -> date:
        """Последний день, когда ещё можно взять транш (не включительно)."""
        stop_months = self.variant(contract.variant).draw_stop_months_before_end
        if stop_months is not None:
            return add_months(contract.end_date, -stop_months)
        return contract.end_date

    def draw_errors(
        self, contract: CreditLineContract, amount: Money, today: date
    ) -> list[FieldError]:
        errors = []
        if amount < self.tranche_min:
            errors.append(
                FieldError("amount", "TRANCHE_BELOW_MIN", f"Транш от {self.tranche_min} ₽")
            )
        if amount > contract.available:
            errors.append(
                FieldError("amount", "OVER_AVAILABLE", f"Доступно {contract.available} ₽")
            )
        if not contract.start_date <= today < self.draw_until(contract):
            errors.append(
                FieldError("amount", "DRAW_WINDOW_CLOSED", "Окно выборки траншей закрыто")
            )
        return errors

    def can_draw(self, contract: CreditLineContract, amount: Money, today: date) -> bool:
        return contract.is_active() and not self.draw_errors(contract, amount, today)
