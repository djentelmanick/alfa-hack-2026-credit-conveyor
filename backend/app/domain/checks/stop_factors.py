"""Стоп-факторы SCOPE раздел 4, этап 2. Любой сработавший означает отказ независимо от скоринга."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from decimal import Decimal
from typing import ClassVar

from app.domain.checks.context import CheckingData
from app.domain.entities import CheckResult
from app.domain.enums import CheckOutcome, CheckStage, CompanyStatus, KycLevel, SourceType
from app.domain.products import ProductCatalog
from app.domain.value_objects import BailiffInfo, CompanyProfile, TaxInfo

NO_ANSWER = "нет ответа"


class StopFactorRule(ABC):
    code: ClassVar[str]
    expected: ClassVar[str]
    requires: ClassVar[frozenset[SourceType]]

    def check(self, data: CheckingData) -> CheckResult:
        if self.requires & data.missing:
            return self._result(NO_ANSWER, CheckOutcome.NO_DATA)
        actual, stopped = self._evaluate(data)
        return self._result(actual, CheckOutcome.STOP_FACTOR if stopped else CheckOutcome.PASSED)

    def _result(self, actual: str, outcome: CheckOutcome) -> CheckResult:
        return CheckResult(
            code=self.code,
            stage=CheckStage.CHECKING,
            expected=self.expected,
            actual=actual,
            outcome=outcome,
        )

    @abstractmethod
    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        """Что нашли (для отчёта аналитику) и сработал ли стоп-фактор.

        Вызывается, только если все источники из requires ответили.
        """


def _company(data: CheckingData) -> CompanyProfile:
    return _required(data.company, SourceType.EGRUL)


def _tax(data: CheckingData) -> TaxInfo:
    return _required(data.tax, SourceType.FNS)


def _bailiff(data: CheckingData) -> BailiffInfo:
    return _required(data.bailiff, SourceType.FSSP)


def _required[T](value: T | None, source: SourceType) -> T:
    # Источник не в missing, а данных нет: сборщик нарушил контракт CheckingData.
    if value is None:
        raise ValueError(f"Источник {source} ответил, но данных в CheckingData нет")
    return value


class CompanyInactiveRule(StopFactorRule):
    code = "COMPANY_INACTIVE"
    expected = "компания действует"
    requires = frozenset({SourceType.EGRUL})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        status = _company(data).status
        return f"статус {status}", status is not CompanyStatus.ACTIVE


class BusinessAgeUnder6mRule(StopFactorRule):
    MIN_AGE_MONTHS: ClassVar[int] = 6
    code = "BUSINESS_AGE_UNDER_6M"
    expected = f"бизнесу не меньше {MIN_AGE_MONTHS} мес"
    requires = frozenset({SourceType.EGRUL})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        age = _company(data).age_months(data.today)
        return f"{age} мес", age < self.MIN_AGE_MONTHS


class CurrentOverdueOver30Rule(StopFactorRule):
    MAX_OVERDUE_DAYS: ClassVar[int] = 30
    MIN_FOUNDER_SHARE_PCT: ClassVar[Decimal] = Decimal(20)
    code = "CURRENT_OVERDUE_OVER_30"
    expected = (
        f"текущая просрочка не больше {MAX_OVERDUE_DAYS} дн. у компании, директора "
        f"и учредителей с долей от {MIN_FOUNDER_SHARE_PCT}%"
    )
    # Без ЕГРЮЛ неизвестны директор и учредители.
    requires = frozenset({SourceType.EGRUL, SourceType.BKI})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        company = _company(data)
        credit_report = _required(data.credit_report, SourceType.BKI)
        subjects = {company.inn, company.director_inn} | {
            f.inn for f in company.founders if f.share_pct >= self.MIN_FOUNDER_SHARE_PCT
        }
        reports = [
            r for r in (credit_report, *data.related_credit_reports) if r.subject_inn in subjects
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
    requires = frozenset({SourceType.EGRUL})

    def __init__(self, forbidden_codes: Sequence[str]) -> None:
        self._forbidden = tuple(forbidden_codes)

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        okved = _company(data).okved
        # Код из конфига задаёт группу: «92» запрещает и «92.11», но не «29.2».
        forbidden = any(okved == code or okved.startswith(f"{code}.") for code in self._forbidden)
        return f"ОКВЭД {okved}", forbidden


class TaxBlockOrBailiffRule(StopFactorRule):
    code = "TAX_BLOCK_OR_BAILIFF"
    expected = "нет блокировки счетов ФНС и неисполненных производств ФССП"
    requires = frozenset({SourceType.FNS, SourceType.FSSP})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        tax, bailiff = _tax(data), _bailiff(data)
        found: list[str] = []
        if tax.accounts_blocked:
            found.append("операции по счетам приостановлены ФНС")
        if bailiff.open_proceedings > 0:
            found.append(f"производств ФССП: {bailiff.open_proceedings} на {bailiff.total_debt}")
        return ("; ".join(found), True) if found else ("нет", False)


class CbrRedLevelRule(StopFactorRule):
    code = "CBR_RED_LEVEL"
    expected = "уровень риска ЦБ не красный"
    requires = frozenset({SourceType.CBR_KYC})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        level = _required(data.cbr_level, SourceType.CBR_KYC)
        return f"уровень {level}", level is KycLevel.RED


class RosfinListRule(StopFactorRule):
    code = "ROSFIN_LIST"
    expected = "компании, директора и учредителей нет в перечне Росфинмониторинга"
    requires = frozenset({SourceType.ROSFIN})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        hit = _required(data.rosfin_hit, SourceType.ROSFIN)
        return ("найдены в перечне", True) if hit else ("не найдены", False)


class DirectorDisqualifiedRule(StopFactorRule):
    code = "DIRECTOR_DISQUALIFIED"
    expected = "руководитель не дисквалифицирован"
    requires = frozenset({SourceType.FNS})

    def _evaluate(self, data: CheckingData) -> tuple[str, bool]:
        if _tax(data).director_disqualified:
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
