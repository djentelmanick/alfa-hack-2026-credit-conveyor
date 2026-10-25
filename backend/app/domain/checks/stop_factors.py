"""Стоп-факторы SCOPE раздел 4, этап 2. Любой сработавший означает отказ независимо от скоринга."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from decimal import Decimal
from typing import ClassVar

from app.domain.checks.context import PipelineContext
from app.domain.entities import CheckResult
from app.domain.enums import CheckOutcome, CheckStage, CompanyStatus, KycLevel
from app.domain.products import ProductCatalog


class StopFactorRule(ABC):
    code: ClassVar[str]
    expected: ClassVar[str]

    def check(self, ctx: PipelineContext) -> CheckResult:
        actual, stopped = self._evaluate(ctx)
        return CheckResult(
            code=self.code,
            stage=CheckStage.CHECKING,
            expected=self.expected,
            actual=actual,
            outcome=CheckOutcome.STOP_FACTOR if stopped else CheckOutcome.PASSED,
        )

    @abstractmethod
    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        """Что нашли (для отчёта аналитику) и сработал ли стоп-фактор."""


class CompanyInactiveRule(StopFactorRule):
    code = "COMPANY_INACTIVE"
    expected = "компания действует"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        status = ctx.company.status
        return f"статус {status}", status is not CompanyStatus.ACTIVE


class BusinessAgeUnder6mRule(StopFactorRule):
    MIN_AGE_MONTHS: ClassVar[int] = 6
    code = "BUSINESS_AGE_UNDER_6M"
    expected = f"бизнесу не меньше {MIN_AGE_MONTHS} мес"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        age = ctx.company.age_months(ctx.today)
        return f"{age} мес", age < self.MIN_AGE_MONTHS


class CurrentOverdueOver30Rule(StopFactorRule):
    MAX_OVERDUE_DAYS: ClassVar[int] = 30
    MIN_FOUNDER_SHARE_PCT: ClassVar[Decimal] = Decimal(20)
    code = "CURRENT_OVERDUE_OVER_30"
    expected = (
        f"текущая просрочка не больше {MAX_OVERDUE_DAYS} дн. у компании, директора "
        f"и учредителей с долей от {MIN_FOUNDER_SHARE_PCT}%"
    )

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        company = ctx.company
        subjects = {company.inn, company.director_inn} | {
            f.inn for f in company.founders if f.share_pct >= self.MIN_FOUNDER_SHARE_PCT
        }
        reports = [
            r for r in (ctx.credit_report, *ctx.related_credit_reports) if r.subject_inn in subjects
        ]
        overdue = [r for r in reports if r.current_overdue_days > self.MAX_OVERDUE_DAYS]
        if overdue:
            found = ", ".join(f"ИНН {r.subject_inn}: {r.current_overdue_days} дн." for r in overdue)
            return found, True
        worst = max((r.current_overdue_days for r in reports), default=0)
        return f"максимальная текущая просрочка {worst} дн.", False


class ForbiddenOkvedRule(StopFactorRule):
    code = "FORBIDDEN_OKVED"
    expected = "отрасль не запрещена (игорный бизнес, производство алкоголя)"

    def __init__(self, forbidden_codes: Sequence[str]) -> None:
        self._forbidden = tuple(forbidden_codes)

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        okved = ctx.company.okved
        # Код из конфига задаёт группу: «92» запрещает и «92.11», но не «29.2».
        forbidden = any(okved == code or okved.startswith(f"{code}.") for code in self._forbidden)
        return f"ОКВЭД {okved}", forbidden


class TaxBlockOrBailiffRule(StopFactorRule):
    code = "TAX_BLOCK_OR_BAILIFF"
    expected = "нет блокировки счетов ФНС и неисполненных производств ФССП"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        found: list[str] = []
        if ctx.tax.accounts_blocked:
            found.append("операции по счетам приостановлены ФНС")
        if ctx.bailiff.open_proceedings > 0:
            found.append(
                f"производств ФССП: {ctx.bailiff.open_proceedings} на {ctx.bailiff.total_debt}"
            )
        return ("; ".join(found), True) if found else ("нет", False)


class CbrRedLevelRule(StopFactorRule):
    code = "CBR_RED_LEVEL"
    expected = "уровень риска ЦБ не красный"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        return f"уровень {ctx.cbr_level}", ctx.cbr_level is KycLevel.RED


class RosfinListRule(StopFactorRule):
    code = "ROSFIN_LIST"
    expected = "компании, директора и учредителей нет в перечне Росфинмониторинга"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        return ("найдены в перечне", True) if ctx.rosfin_hit else ("не найдены", False)


class DirectorDisqualifiedRule(StopFactorRule):
    code = "DIRECTOR_DISQUALIFIED"
    expected = "руководитель не дисквалифицирован"

    def _evaluate(self, ctx: PipelineContext) -> tuple[str, bool]:
        if ctx.tax.director_disqualified:
            return "руководитель дисквалифицирован", True
        return "не дисквалифицирован", False


def default_stop_rules(catalog: ProductCatalog) -> list[StopFactorRule]:
    """Порядок как в SCOPE: отчёт показывает первый сработавший стоп-фактор."""
    return [
        CompanyInactiveRule(),
        BusinessAgeUnder6mRule(),
        CurrentOverdueOver30Rule(),
        ForbiddenOkvedRule(catalog.config.forbidden_okved),
        TaxBlockOrBailiffRule(),
        CbrRedLevelRule(),
        RosfinListRule(),
        DirectorDisqualifiedRule(),
    ]
