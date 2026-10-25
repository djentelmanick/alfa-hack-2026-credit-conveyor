"""Выписка из другого банка: хватает ли периода и подлинна ли она (SCOPE, разделы 3 и 4)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import ClassVar

from app.domain.checks.context import PipelineContext, Verification
from app.domain.dates import add_months
from app.domain.entities import BankStatement, CheckResult
from app.domain.enums import CheckOutcome, CheckStage, CompanyKind, SignatureStatus
from app.domain.labels import SIGNATURE_STATUS_LABELS
from app.domain.value_objects import CompanyProfile


def _result(code: str, expected: str, actual: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(
        code=code, stage=CheckStage.CHECKING, expected=expected, actual=actual, outcome=outcome
    )


def _passed(ok: bool) -> CheckOutcome:
    return CheckOutcome.PASSED if ok else CheckOutcome.FAILED


class AuthenticityStrategy(ABC):
    code: ClassVar[str]

    @abstractmethod
    def supports(self, company: CompanyProfile) -> bool: ...

    @abstractmethod
    def verify(self, statement: BankStatement, ctx: PipelineContext) -> CheckResult: ...


class BankSignatureStrategy(AuthenticityStrategy):
    """Подпись проверяет порт ЭП при загрузке файла, здесь читаем её результат."""

    code = "BANK_SIGNATURE"

    def supports(self, company: CompanyProfile) -> bool:
        return True

    def verify(self, statement: BankStatement, ctx: PipelineContext) -> CheckResult:
        status = statement.signature_status
        return _result(
            self.code,
            "подпись банка есть и верна",
            f"подпись {SIGNATURE_STATUS_LABELS[status]}",
            _passed(status is SignatureStatus.VALID),
        )


class FnsRevenueReconciliationStrategy(AuthenticityStrategy):
    """Сверка с ГИР БО. У ИП бухгалтерской отчётности нет, поэтому только ООО."""

    MAX_DEVIATION_PCT: ClassVar[Decimal] = Decimal(20)
    code = "FNS_REVENUE_MATCH"

    def supports(self, company: CompanyProfile) -> bool:
        return company.kind is CompanyKind.LLC

    def verify(self, statement: BankStatement, ctx: PipelineContext) -> CheckResult:
        expected = (
            f"выручка по выписке отличается от ГИР БО не больше чем на {self.MAX_DEVIATION_PCT}%"
        )
        reported = ctx.reported_annual_revenue
        if reported is None or not reported.is_positive():
            return _result(self.code, expected, "нет отчётности в ГИР БО", CheckOutcome.NO_DATA)
        by_statement = statement.annual_revenue()
        deviation_pct = abs(by_statement.amount - reported.amount) * 100 / reported.amount
        actual = f"по выписке {by_statement}, в ГИР БО {reported}, расхождение {deviation_pct:.1f}%"
        return _result(
            self.code, expected, actual, _passed(deviation_pct <= self.MAX_DEVIATION_PCT)
        )


class StatementVerifier:
    MIN_MONTHS: ClassVar[int] = 6
    MAX_STALENESS_MONTHS: ClassVar[int] = 1
    PERIOD_CODE: ClassVar[str] = "STATEMENT_PERIOD"

    def __init__(self, strategies: Sequence[AuthenticityStrategy]) -> None:
        self._strategies = tuple(strategies)

    def check_period(self, statement: BankStatement, today: date) -> CheckResult:
        """FAILED означает дозапрос выписки, а не отказ."""
        months = statement.months_covered()
        last = statement.last_operation_date()
        fresh_since = add_months(today, -self.MAX_STALENESS_MONTHS)
        ok = months >= self.MIN_MONTHS and last is not None and last >= fresh_since
        last_text = last.isoformat() if last is not None else "операций нет"
        return _result(
            self.PERIOD_CODE,
            f"выписка не короче {self.MIN_MONTHS} мес, "
            f"последняя операция не раньше {fresh_since.isoformat()}",
            f"{months} мес, последняя операция {last_text}",
            _passed(ok),
        )

    def verify(self, statement: BankStatement, ctx: PipelineContext) -> Verification:
        checks = tuple(
            s.verify(statement, ctx) for s in self._strategies if s.supports(ctx.company)
        )
        return Verification(verified=any(c.passed for c in checks), checks=checks)


def default_authenticity_strategies() -> list[AuthenticityStrategy]:
    return [BankSignatureStrategy(), FnsRevenueReconciliationStrategy()]
