"""Моки внешних источников: переводят синтетику в формате docs/data-sources в доменные объекты."""

import hashlib
import re
from collections.abc import Sequence
from decimal import Decimal

from app.domain.entities import Consent
from app.domain.enums import CompanyKind, CompanyStatus, KycLevel, SignatureStatus, SourceType
from app.domain.errors import NotFound
from app.domain.ports import (
    AccountTurnoverPort,
    BailiffPort,
    CbrKycPort,
    CompanyRegistryPort,
    CreditBureauPort,
    PassportCheckPort,
    RosfinmonitoringPort,
    SignatureVerifierPort,
    TaxServicePort,
)
from app.domain.value_objects import (
    BailiffInfo,
    CompanyProfile,
    CreditReport,
    Founder,
    Money,
    MonthlyTurnover,
    MonthRevenue,
    Payer,
    TaxInfo,
)
from app.infra.mocks.base import MockSourceAdapter
from app.infra.mocks.synthetic import BkiReport, EgrulRecord, TurnoverMonth, normalize_text
from app.infra.parsers.statement_1c import BankSignatureBlock, decode_1c

_STATUS = {
    "active": CompanyStatus.ACTIVE,
    # Статуса «в процессе ликвидации» в домене нет, а для стоп-фактора 1 это та же ликвидация.
    "liquidating": CompanyStatus.LIQUIDATED,
    "liquidated": CompanyStatus.LIQUIDATED,
    "bankrupt": CompanyStatus.BANKRUPT,
    "reorganizing": CompanyStatus.REORGANIZING,
}

_KYC = {"green": KycLevel.GREEN, "yellow": KycLevel.YELLOW, "red": KycLevel.RED}


class MockEgrulAdapter(MockSourceAdapter, CompanyRegistryPort):
    async def find_by_inn(self, inn: str) -> CompanyProfile:
        await self._respond(SourceType.EGRUL, inn)
        company = self._fixtures.company(inn)
        if company is None:
            raise NotFound("ИНН не найден в ЕГРЮЛ/ЕГРИП")
        return _company_profile(company.egrul)


def _company_profile(record: EgrulRecord) -> CompanyProfile:
    if record.entity_type == "individual":
        return CompanyProfile(
            inn=record.inn,
            kind=CompanyKind.IE,
            name=f"ИП {record.full_name}",
            ogrn=record.ogrnip or "",
            registered_at=record.registration_date,
            status=_STATUS[record.status],
            okved=record.primary_okved.code,
            okved_label=record.primary_okved.name,
            director=record.full_name,
            director_inn=record.inn,
            address=record.address,
            unreliable_records=record.unreliable_fields,
        )
    if record.director is None or record.ogrn is None:
        raise ValueError(f"{record.inn}: у юрлица в ЕГРЮЛ нет руководителя или ОГРН")
    # В домене одно поле на повод 4 «смена директора или собственника», поэтому берём
    # самую позднюю запись о руководителе или учредителе, если она позже регистрации.
    latest_change = max(
        (record.director.record_date, *(f.record_date for f in record.founders)),
    )
    return CompanyProfile(
        inn=record.inn,
        kind=CompanyKind.LLC,
        name=record.short_name or record.full_name,
        ogrn=record.ogrn,
        registered_at=record.registration_date,
        status=_STATUS[record.status],
        okved=record.primary_okved.code,
        okved_label=record.primary_okved.name,
        director=record.director.full_name,
        director_inn=record.director.inn,
        address=record.address,
        founders=tuple(
            Founder(full_name=f.full_name, inn=f.inn, share_pct=f.share_percent)
            for f in record.founders
        ),
        director_changed_at=latest_change if latest_change > record.registration_date else None,
        unreliable_records=record.unreliable_fields,
    )


class MockBkiAdapter(MockSourceAdapter, CreditBureauPort):
    async def report(self, subject_inn: str, consent: Consent) -> CreditReport:
        # Согласие проверяет конвейер до запроса, мок по документации считает его полученным.
        await self._respond(SourceType.BKI, subject_inn)
        raw = self._fixtures.credit_report(subject_inn)
        if raw is None:
            return CreditReport(
                subject_inn=subject_inn,
                has_history=False,
                current_overdue_days=0,
                max_closed_overdue_days_last_year=0,
                monthly_payments=Money.zero(),
                has_closed_loans_without_overdue=False,
                inquiries_last_3_months=0,
            )
        return _credit_report(raw)


def _credit_report(raw: BkiReport) -> CreditReport:
    open_credits = [c for c in raw.credits if c.status == "open"]
    return CreditReport(
        subject_inn=raw.subject_inn,
        has_history=raw.summary.has_history,
        current_overdue_days=max((c.overdue_days for c in open_credits), default=0),
        # Текущая просрочка ещё не закрыта, поэтому «закрытые за год» считаем по кредитам без неё.
        max_closed_overdue_days_last_year=max(
            (c.max_overdue_days_12m for c in raw.credits if c.overdue_days == 0), default=0
        ),
        monthly_payments=Money(sum((c.monthly_payment for c in open_credits), Decimal(0))),
        has_closed_loans_without_overdue=any(
            c.status == "closed" and c.max_overdue_days_12m == 0 and not c.had_overdue_30plus_12m
            for c in raw.credits
        ),
        inquiries_last_3_months=raw.summary.inquiries_last_3m,
    )


class MockTurnoverAdapter(MockSourceAdapter, AccountTurnoverPort):
    async def monthly_turnover(self, inn: str, months: int) -> MonthlyTurnover | None:
        if months <= 0:
            raise ValueError("Число месяцев должно быть положительным")
        await self._respond(SourceType.TURNOVER, inn)
        company = self._fixtures.company(inn)
        if company is None or not company.turnover.has_account:
            return None
        recent = sorted(company.turnover.months, key=lambda m: m.month)[-months:]
        return MonthlyTurnover(inn=inn, months=tuple(_month_revenue(m) for m in recent))


def _month_revenue(month: TurnoverMonth) -> MonthRevenue:
    return MonthRevenue(
        month=month.month,
        incoming=Money(month.incoming),
        outgoing=Money(month.outgoing),
        top_payers=tuple(
            Payer(inn=p.inn, name=p.name, amount=Money(p.amount), share_pct=p.share_percent)
            for p in month.top_payers
        ),
    )


class MockFnsAdapter(MockSourceAdapter, TaxServicePort):
    """ФНС и «Прозрачный бизнес» (SourceType.FNS) и ГИР БО (SourceType.GIR_BO)."""

    async def account_blocks(self, inn: str) -> bool:
        await self._respond(SourceType.FNS, inn)
        company = self._fixtures.company(inn)
        return company is not None and company.fns_transparent.accounts_blocked

    async def is_mass_address(self, address: str) -> bool:
        await self._respond(None)
        return normalize_text(address) in self._fixtures.mass_addresses()

    async def annual_revenue(self, inn: str, year: int) -> Money | None:
        await self._respond(SourceType.GIR_BO, inn)
        company = self._fixtures.company(inn)
        if company is None:
            return None
        report = company.gir_bo.get(year)
        if report is None:
            return None
        # ГИР БО отдаёт суммы в тысячах рублей.
        return Money(report.income_statement.revenue * 1000)

    async def tax_info(self, inn: str) -> TaxInfo:
        await self._respond(SourceType.FNS, inn)
        company = self._fixtures.company(inn)
        if company is None:
            raise NotFound("ИНН не найден в «Прозрачном бизнесе»")
        raw = company.fns_transparent
        # «Прозрачный бизнес» показывает долг двумя суммами: недоимка с пенями и штрафами
        # и часть долга, переданная на взыскание. Они пересекаются: берём большую, а не сумму.
        if raw.tax_debt_enforcement > raw.arrears_and_penalties:
            debt, debt_date = raw.tax_debt_enforcement, raw.tax_debt_enforcement_date
        else:
            debt, debt_date = raw.arrears_and_penalties, raw.arrears_and_penalties_date
        return TaxInfo(
            inn=inn,
            accounts_blocked=raw.accounts_blocked,
            mass_address=raw.mass_address,
            mass_director=raw.director is not None and raw.director.is_mass,
            mass_founder=raw.has_mass_founder,
            director_disqualified=raw.director_disqualified,
            tax_debt=Money(debt),
            data_date=debt_date,
        )


class MockFsspAdapter(MockSourceAdapter, BailiffPort):
    async def open_proceedings(self, inn: str) -> BailiffInfo:
        # Настоящая ФССП ищет физлицо по ФИО и дате рождения, но порт принимает ИНН,
        # а синтетика у нас по ИНН руководителя.
        await self._respond(SourceType.FSSP, inn)
        raw = self._fixtures.bailiff_report(inn)
        active = [] if raw is None else [c for c in raw.cases if c.status == "active"]
        return BailiffInfo(
            subject_inn=inn,
            open_proceedings=len(active),
            total_debt=Money(sum((c.amount for c in active), Decimal(0))),
        )


class MockCbrAdapter(MockSourceAdapter, CbrKycPort):
    async def risk_level(self, inn: str) -> KycLevel:
        await self._respond(SourceType.CBR_KYC, inn)
        company = self._fixtures.company(inn)
        # По документации источника мок по умолчанию зелёный.
        return KycLevel.GREEN if company is None else _KYC[company.cbr_kyc.risk_level]


class MockRosfinAdapter(MockSourceAdapter, RosfinmonitoringPort):
    async def is_listed(self, inn: str, persons: Sequence[str]) -> bool:
        await self._respond(SourceType.ROSFIN, inn)
        registry = self._fixtures.rosfin
        if any(org.inn == inn for org in registry.organizations):
            return True
        listed = {normalize_text(p.full_name) for p in registry.persons}
        return any(normalize_text(name) in listed for name in persons)


_PASSPORT_SERIES = re.compile(r"[0-9]{4}")
_PASSPORT_NUMBER = re.compile(r"[0-9]{6}")
_INN_FIELD = re.compile(r"ИНН=([0-9]{10}|[0-9]{12})\s*$", re.MULTILINE)


class MockSignatureAdapter(MockSourceAdapter, SignatureVerifierPort):
    """Подпись банка в синтетических выписках это блок СекцияПодписьБанка с хешем тела файла.

    Настоящая проверка УКЭП (КриптоПро) встанет за этим же портом.
    """

    async def verify(self, statement_raw: bytes) -> SignatureStatus:
        text = decode_1c(statement_raw)
        await self._respond(SourceType.BANK_SIGNATURE, *set(_INN_FIELD.findall(text)))
        block = BankSignatureBlock.find(statement_raw)
        if block is None:
            return SignatureStatus.ABSENT
        actual = hashlib.sha256(block.signed_content).hexdigest()
        return SignatureStatus.VALID if actual == block.content_hash else SignatureStatus.INVALID


class MockPassportAdapter(MockSourceAdapter, PassportCheckPort):
    async def is_valid(self, series: str, number: str) -> bool:
        await self._respond(None)
        if not (_PASSPORT_SERIES.fullmatch(series) and _PASSPORT_NUMBER.fullmatch(number)):
            return False
        return not any(
            p.series == series and p.number == number for p in self._fixtures.passports.invalid
        )
