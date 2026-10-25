"""Что банк нашёл по источникам, в том виде, в каком это видит клиент (API, раздел 2.4).

Проверок, сравнений и решений банка здесь нет. Всё строится из снимков в PostgreSQL,
в источники повторно не ходим.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.source_payload import (
    company_from_payload,
    credit_report_from_payload,
    money_from_payload,
    turnover_from_payload,
)
from app.domain.entities import Application, SourceSnapshot
from app.domain.enums import FetchStatus, SourceType
from app.domain.value_objects import CompanyProfile, CreditReport, Money, MonthlyTurnover


@dataclass(frozen=True, slots=True)
class AnnualReport:
    year: int
    revenue: Money


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicationFindings:
    application_id: UUID
    collected_at: datetime
    company: CompanyProfile | None = None
    credit_load: CreditReport | None = None
    account_turnover: MonthlyTurnover | None = None
    annual_report: AnnualReport | None = None


class FindingsBuilder:
    def build(self, app: Application) -> ApplicationFindings:
        """Раздел не ответившего источника просто отсутствует."""
        if not app.snapshots:
            raise ValueError(f"У заявки {app.id} нет собранных данных")
        ok = [s for s in app.snapshots if s.status is FetchStatus.OK]
        return ApplicationFindings(
            application_id=app.id,
            collected_at=max(s.fetched_at for s in app.snapshots),
            company=_first(ok, SourceType.EGRUL, company_from_payload),
            credit_load=_own_credit_report(ok, app.inn),
            account_turnover=_turnover(ok),
            annual_report=_annual_report(ok),
        )


type _Parser[T] = Callable[[Mapping[str, object]], T | None]


def _first[T](snapshots: list[SourceSnapshot], source: SourceType, parse: _Parser[T]) -> T | None:
    for s in snapshots:
        if s.source is source and (value := parse(s.payload)) is not None:
            return value
    return None


def _own_credit_report(snapshots: list[SourceSnapshot], inn: str) -> CreditReport | None:
    # У БКИ снимков несколько: компания, директор, учредители. Клиенту показываем только компанию.
    for s in snapshots:
        if s.source is SourceType.BKI:
            report = credit_report_from_payload(s.payload)
            if report is not None and report.subject_inn == inn:
                return report
    return None


def _turnover(snapshots: list[SourceSnapshot]) -> MonthlyTurnover | None:
    turnover = _first(snapshots, SourceType.TURNOVER, turnover_from_payload)
    return turnover if turnover is not None and turnover.months else None


def _annual_report(snapshots: list[SourceSnapshot]) -> AnnualReport | None:
    for s in snapshots:
        if s.source is SourceType.GIR_BO and (revenue := money_from_payload(s.payload)):
            # Конвейер запрашивает ГИР БО за прошлый год от момента сбора.
            return AnnualReport(year=s.fetched_at.year - 1, revenue=revenue)
    return None
