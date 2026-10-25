from dataclasses import dataclass
from datetime import date, datetime

from app.domain.entities import Application, BankStatement, CheckResult
from app.domain.enums import KycLevel, SourceType
from app.domain.value_objects import (
    BailiffInfo,
    CompanyProfile,
    CreditReport,
    Money,
    MonthlyTurnover,
    TaxInfo,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class Verification:
    """Результат проверки подлинности выписки: подтверждена, если её подтвердила любая стратегия."""

    verified: bool
    checks: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class CheckingData:
    """То, что удалось собрать. Поле не ответившего или не запрошенного источника пустое,
    а сам источник лежит в missing: по этим данным стоп-факторы считаются и при сбоях,
    чтобы аналитик видел в отчёте всё, что нашлось (SCOPE, раздел 4).
    """

    application: Application
    company: CompanyProfile | None = None
    credit_report: CreditReport | None = None
    related_credit_reports: tuple[CreditReport, ...] = ()
    turnover: MonthlyTurnover | None = None
    statement: BankStatement | None = None
    tax: TaxInfo | None = None
    bailiff: BailiffInfo | None = None
    director_bailiff: BailiffInfo | None = None
    cbr_level: KycLevel | None = None
    rosfin_hit: bool | None = None
    reported_annual_revenue: Money | None = None
    missing: frozenset[SourceType] = frozenset()
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()


@dataclass(frozen=True, kw_only=True, slots=True)
class PipelineContext:
    """Всё, что конвейер собрал по заявке. Проверки, скоринг и решение читают только его.

    Данные собираются заранее, поэтому правила синхронные и не ходят в порты.
    """

    application: Application
    company: CompanyProfile
    credit_report: CreditReport
    # Директор и учредители. Кого из них учитывать (доля от 20%), решает само правило.
    related_credit_reports: tuple[CreditReport, ...] = ()
    turnover: MonthlyTurnover | None
    statement: BankStatement | None = None
    tax: TaxInfo
    bailiff: BailiffInfo
    # Только у ООО: у ИП физлицо и есть компания, его производства уже в bailiff.
    director_bailiff: BailiffInfo | None = None
    cbr_level: KycLevel
    rosfin_hit: bool
    reported_annual_revenue: Money | None = None
    statement_verification: Verification | None = None
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()

    def revenue_history_months(self) -> int:
        if self.turnover is not None and self.turnover.months:
            return len(self.turnover.months)
        if self.statement is not None:
            return self.statement.months_covered()
        return 0

    def annual_revenue(self) -> Money:
        """Обороты по счёту в нашем банке, иначе выписка; оба приводятся к году."""
        if self.turnover is not None and self.turnover.months:
            return self.turnover.total() * 12 / len(self.turnover.months)
        if self.statement is not None:
            return self.statement.annual_revenue()
        return Money.zero()

    def monthly_revenue(self) -> Money:
        return self.annual_revenue() / 12
