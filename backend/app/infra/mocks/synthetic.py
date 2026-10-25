"""Синтетика для моков: схемы повторяют контракты из docs/data-sources, по одному файлу на ИНН."""

import json
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


class _Raw(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- ЕГРЮЛ / ЕГРИП (01-egrul-egrip.md) ---


class Okved(_Raw):
    code: str
    name: str


class EgrulDirector(_Raw):
    full_name: str
    inn: str
    position: str
    record_date: date


class EgrulFounder(_Raw):
    full_name: str
    inn: str
    share_percent: Decimal
    share_amount: Decimal
    record_date: date


class EgrulRecord(_Raw):
    inn: str
    ogrn: str | None
    ogrnip: str | None
    full_name: str
    short_name: str | None
    kpp: str | None
    registration_date: date
    status: Literal["active", "liquidating", "liquidated", "bankrupt", "reorganizing"]
    address: str
    entity_type: Literal["legal", "individual"]
    authorized_capital: Decimal | None
    director: EgrulDirector | None
    founders: tuple[EgrulFounder, ...]
    primary_okved: Okved
    additional_okveds: tuple[Okved, ...]
    has_unreliable_info: bool
    unreliable_fields: tuple[Literal["address", "director", "founder"], ...]


# --- БКИ (02-bki.md) ---


class BkiCredit(_Raw):
    contract_id: str
    creditor_name: str
    credit_type: Literal["credit", "credit_line", "mortgage", "microfinance"]
    amount: Decimal
    balance: Decimal
    monthly_payment: Decimal
    opened_date: date
    closed_date: date | None
    status: Literal["open", "closed"]
    overdue_days: int = Field(ge=0)
    overdue_amount: Decimal
    max_overdue_days_12m: int = Field(ge=0)
    had_overdue_30plus_12m: bool


class BkiInquiry(_Raw):
    date: date
    creditor_name: str
    amount: Decimal
    purpose: str


class BkiSummary(_Raw):
    total_monthly_payments: Decimal
    has_current_overdue_30plus: bool
    has_closed_without_overdue: bool
    worst_status_12m: Literal[
        "clean",
        "overdue_under_30",
        "overdue_over_30",
        "current_overdue_under_30",
        "current_overdue_over_30",
    ]
    inquiries_last_3m: int = Field(ge=0)
    has_history: bool


class BkiReport(_Raw):
    subject_inn: str
    subject_type: Literal["company", "person"]
    report_date: date
    credits: tuple[BkiCredit, ...]
    inquiries: tuple[BkiInquiry, ...]
    summary: BkiSummary


# --- Обороты по счёту (03-account-turnover.md) ---


class TopPayer(_Raw):
    name: str
    inn: str
    amount: Decimal
    share_percent: Decimal


class TurnoverMonth(_Raw):
    month: date
    incoming: Decimal
    outgoing: Decimal
    top_payers: tuple[TopPayer, ...]


class TurnoverSummary(_Raw):
    annual_revenue: Decimal
    avg_monthly_revenue: Decimal


class AccountTurnover(_Raw):
    inn: str
    has_account: bool
    months: tuple[TurnoverMonth, ...]
    summary: TurnoverSummary | None


# --- «Прозрачный бизнес» (06-fns-transparent-business.md) ---


class FnsPerson(_Raw):
    full_name: str
    inn: str
    company_count: int = Field(ge=0)
    is_mass: bool


class DisqualificationPeriod(_Raw):
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")


class FnsTransparent(_Raw):
    inn: str
    # В контракте data-sources этого поля нет: решения о приостановлении операций ФНС публикует
    # в отдельном сервисе, а для мока стоп-фактора 5 его удобно держать рядом с данными ФНС.
    accounts_blocked: bool
    mass_address: bool
    address_company_count: int | None
    director: FnsPerson | None
    director_disqualified: bool
    disqualification_period: DisqualificationPeriod | None
    founders: tuple[FnsPerson, ...]
    has_mass_founder: bool
    tax_debt_enforcement: Decimal
    tax_debt_enforcement_date: date
    arrears_and_penalties: Decimal
    arrears_and_penalties_date: date
    tax_reporting_missing: bool
    employee_count: int | None
    employee_count_year: int | None
    taxes_paid: Decimal | None
    revenue_accounting: Decimal | None
    expense_accounting: Decimal | None


# --- ГИР БО (07-gir-bo.md), суммы в тысячах рублей ---


class GirBoBalance(_Raw):
    total_assets: Decimal
    fixed_assets: Decimal
    inventories: Decimal
    cash: Decimal
    financial_assets: Decimal
    equity: Decimal
    long_term_debt: Decimal
    short_term_payables: Decimal


class GirBoIncomeStatement(_Raw):
    revenue: Decimal
    expenses: Decimal
    other_income: Decimal
    other_expenses: Decimal
    income_tax: Decimal
    net_profit: Decimal


class GirBoPreviousYear(_Raw):
    revenue: Decimal
    net_profit: Decimal


class GirBoReport(_Raw):
    inn: str
    company_name: str
    year: int
    unit: Literal["thousands"]
    balance: GirBoBalance
    income_statement: GirBoIncomeStatement
    previous_year: GirBoPreviousYear
    report_date: date
    signed_by: str
    requires_audit: bool


# --- ФССП (08-fssp.md) ---


class FsspCase(_Raw):
    case_number: str
    date_opened: date
    subject: str
    amount: Decimal
    department: str
    status: Literal["active", "completed", "terminated"]


class FsspReport(_Raw):
    subject: str
    subject_inn: str
    search_type: Literal["company", "person"]
    cases: tuple[FsspCase, ...]
    total_active_amount: Decimal
    has_active_cases: bool


# --- ЦБ «Знай своего клиента» (09-cbr-kyc.md) ---


class CbrKyc(_Raw):
    inn: str
    risk_level: Literal["green", "yellow", "red"]
    risk_level_date: date
    entity_type: Literal["legal", "individual"]


# --- Набор на один ИНН ---


class CompanyFixture(_Raw):
    egrul: EgrulRecord
    bki: tuple[BkiReport, ...]
    turnover: AccountTurnover
    fns_transparent: FnsTransparent
    gir_bo: dict[int, GirBoReport]
    fssp: tuple[FsspReport, ...]
    cbr_kyc: CbrKyc

    @property
    def inn(self) -> str:
        return self.egrul.inn

    def related_inns(self) -> frozenset[str]:
        """Компания, руководитель и учредители: все, по кому источники ищут данные этой компании."""
        inns = {self.inn}
        if self.egrul.director is not None:
            inns.add(self.egrul.director.inn)
        inns.update(founder.inn for founder in self.egrul.founders)
        return frozenset(inns)


# --- Общие справочники ---


class RosfinOrganization(_Raw):
    inn: str
    name: str
    list_type: Literal["terrorist", "extremist"]
    entry_date: date
    reason: str


class RosfinPerson(_Raw):
    full_name: str
    list_type: Literal["terrorist", "extremist"]
    entry_date: date
    reason: str


class RosfinList(_Raw):
    updated_at: date
    organizations: tuple[RosfinOrganization, ...]
    persons: tuple[RosfinPerson, ...]


class InvalidPassport(_Raw):
    series: str
    number: str
    reason: str


class PassportRegistry(_Raw):
    invalid: tuple[InvalidPassport, ...]


# --- Реестры доверенностей (12-fns-mchd.md, 13-fnp-notary-poa.md) ---

PoaPowerCode = Literal["LOAN_APPLICATION", "LOAN_AGREEMENT_SIGNING"]


class PoaParty(_Raw):
    inn: str
    full_name: str


class MchdRecord(_Raw):
    guid: str
    principal: PoaParty
    signed_by: PoaParty
    representative: PoaParty
    issued_at: date
    valid_until: date
    status: Literal["active", "revoked"]
    powers: tuple[PoaPowerCode, ...]


class NotaryPoaRecord(_Raw):
    notarized_on: date
    register_number: str
    notary_name: str
    principal: PoaParty
    signed_by: PoaParty
    representative: PoaParty
    valid_until: date
    revoked: bool
    powers: tuple[PoaPowerCode, ...]


class MchdRegistry(_Raw):
    records: tuple[MchdRecord, ...] = ()


class NotaryPoaRegistry(_Raw):
    records: tuple[NotaryPoaRecord, ...] = ()


class SyntheticData:
    """Синтетика в памяти с индексами по ИНН субъекта: порты БКИ и ФССП спрашивают по физлицу."""

    def __init__(
        self,
        companies: Iterable[CompanyFixture],
        rosfin: RosfinList,
        passports: PassportRegistry,
        mchd: MchdRegistry | None = None,
        notary_poa: NotaryPoaRegistry | None = None,
    ) -> None:
        self._companies: dict[str, CompanyFixture] = {}
        self._credit_reports: dict[str, BkiReport] = {}
        self._bailiff_reports: dict[str, FsspReport] = {}
        self._companies_by_person: dict[str, set[str]] = {}
        for company in companies:
            self._add(company)
        self.rosfin = rosfin
        self.passports = passports
        self.mchd = mchd or MchdRegistry()
        self.notary_poa = notary_poa or NotaryPoaRegistry()

    def _add(self, company: CompanyFixture) -> None:
        if company.inn in self._companies:
            raise ValueError(f"ИНН {company.inn} встречается в синтетике дважды")
        _check_consistency(company)
        self._companies[company.inn] = company
        # Один человек может быть директором или учредителем нескольких компаний,
        # но его кредитная история и производства ФССП от этого не раздваиваются.
        for report in company.bki:
            self._index_once(self._credit_reports, report.subject_inn, report, "БКИ")
        for case in company.fssp:
            self._index_once(self._bailiff_reports, case.subject_inn, case, "ФССП")
        for inn in company.related_inns():
            self._companies_by_person.setdefault(inn, set()).add(company.inn)

    @staticmethod
    def _index_once[T](index: dict[str, T], key: str, value: T, source: str) -> None:
        if key in index and index[key] != value:
            raise ValueError(f"Разные ответы {source} для субъекта {key}")
        index[key] = value

    @classmethod
    def load(cls, data_dir: Path = DEFAULT_DATA_DIR) -> Self:
        companies: list[CompanyFixture] = []
        for path in sorted((data_dir / "companies").glob("*.json")):
            company = CompanyFixture.model_validate(_read_json(path))
            if company.inn != path.stem:
                raise ValueError(f"{path.name}: имя файла не совпадает с ИНН {company.inn}")
            companies.append(company)
        rosfin = RosfinList.model_validate(_read_json(data_dir / "rosfin_list.json"))
        passports = PassportRegistry.model_validate(_read_json(data_dir / "passports.json"))
        mchd = MchdRegistry.model_validate(_read_json(data_dir / "mchd.json"))
        notary_poa = NotaryPoaRegistry.model_validate(_read_json(data_dir / "notary_poa.json"))
        return cls(companies, rosfin, passports, mchd, notary_poa)

    @property
    def companies(self) -> Mapping[str, CompanyFixture]:
        return self._companies

    def company(self, inn: str) -> CompanyFixture | None:
        return self._companies.get(inn)

    def credit_report(self, subject_inn: str) -> BkiReport | None:
        return self._credit_reports.get(subject_inn)

    def bailiff_report(self, subject_inn: str) -> FsspReport | None:
        return self._bailiff_reports.get(subject_inn)

    def companies_of(self, inn: str) -> frozenset[str]:
        """Компании ИНН: он сам компания или в ней директор или учредитель."""
        return frozenset(self._companies_by_person.get(inn, ()))

    def mass_addresses(self) -> frozenset[str]:
        return frozenset(
            normalize_text(c.egrul.address)
            for c in self._companies.values()
            if c.fns_transparent.mass_address
        )


def normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_consistency(company: CompanyFixture) -> None:
    """Сводные поля контрактов дублируют детальные: расхождение это ошибка подготовки синтетики."""
    inn = company.inn
    for section, section_inn in (
        ("turnover", company.turnover.inn),
        ("fns_transparent", company.fns_transparent.inn),
        ("cbr_kyc", company.cbr_kyc.inn),
    ):
        if section_inn != inn:
            raise ValueError(f"{inn}: в секции {section} указан ИНН {section_inn}")
    for year, gir_bo in company.gir_bo.items():
        if gir_bo.inn != inn or gir_bo.year != year:
            raise ValueError(
                f"{inn}: отчётность ГИР БО за {year} не про эту компанию или не за тот год"
            )
    related = company.related_inns()
    subjects = [r.subject_inn for r in company.bki] + [r.subject_inn for r in company.fssp]
    for subject_inn in subjects:
        if subject_inn not in related:
            raise ValueError(f"{inn}: субъект {subject_inn} не связан с компанией")
    for report in company.bki:
        open_payments = sum(
            (c.monthly_payment for c in report.credits if c.status == "open"), Decimal(0)
        )
        if open_payments != report.summary.total_monthly_payments:
            raise ValueError(f"{inn}: платежи БКИ по {report.subject_inn} не сходятся со сводкой")
        if bool(report.credits) != report.summary.has_history:
            raise ValueError(f"{inn}: has_history в БКИ по {report.subject_inn} не сходится")
    turnover = company.turnover
    if turnover.has_account != (turnover.summary is not None):
        raise ValueError(f"{inn}: сводка оборотов есть только при открытом счёте")
    if turnover.summary is not None:
        total = sum((m.incoming for m in turnover.months), Decimal(0))
        if total != turnover.summary.annual_revenue:
            raise ValueError(f"{inn}: годовая выручка в сводке оборотов не равна сумме месяцев")
