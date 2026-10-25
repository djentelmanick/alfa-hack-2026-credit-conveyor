"""Сборка PipelineContext для тестов: вручную по частям или из моков этапа 2."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.checks import PipelineContext, StatementVerifier, default_authenticity_strategies
from app.domain.dates import add_months
from app.domain.entities import Application, BankStatement, Consent, StatementOperation
from app.domain.enums import (
    CollateralType,
    CompanyKind,
    CompanyStatus,
    ConsentType,
    Direction,
    KycLevel,
    Purpose,
    SignatureStatus,
    Variant,
)
from app.domain.value_objects import (
    BailiffInfo,
    Collateral,
    CompanyProfile,
    CreditReport,
    Founder,
    Money,
    MonthlyTurnover,
    MonthRevenue,
    Period,
    TaxInfo,
)
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockRosfinAdapter,
    MockSignatureAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from app.infra.parsers.statement_1c import Statement1CParser
from tests.fakes.scenarios import NOW, TODAY

COMPANY_INN = "7714012340"
DIRECTOR_INN = "771401234587"
FOUNDER_INN = "771402345674"


def make_company(**overrides: Any) -> CompanyProfile:
    base = CompanyProfile(
        inn=COMPANY_INN,
        kind=CompanyKind.LLC,
        name="ООО «Тест»",
        ogrn="1217700000001",
        registered_at=date(2021, 5, 17),
        status=CompanyStatus.ACTIVE,
        okved="56.10",
        director="Петров Пётр Петрович",
        director_inn=DIRECTOR_INN,
        address="г. Москва, ул. Тестовая, д. 1",
        founders=(
            Founder(full_name="Петров Пётр Петрович", inn=DIRECTOR_INN, share_pct=Decimal(70)),
            Founder(full_name="Сидоров Сидор Сидорович", inn=FOUNDER_INN, share_pct=Decimal(30)),
        ),
    )
    return replace(base, **overrides)


def make_collateral(value: str = "9000000") -> Collateral:
    return Collateral(
        type=CollateralType.REAL_ESTATE,
        description="Производственное помещение",
        estimated_value=Money.of(value),
    )


def make_application(
    *,
    inn: str = COMPANY_INN,
    kind: CompanyKind = CompanyKind.LLC,
    purpose: Purpose = Purpose.DEVELOPMENT,
    product_code: str = "LUMP_SUM",
    variant: Variant = Variant.UNSECURED,
    amount: str = "2000000",
    term_months: int = 12,
    owner_guarantee: bool = True,
    collateral: Collateral | None = None,
    consents: tuple[Consent, ...] = (),
    statements: tuple[BankStatement, ...] = (),
) -> Application:
    return Application(
        id=uuid4(),
        applicant_id=uuid4(),
        inn=inn,
        company_kind=kind,
        purpose=purpose,
        product_code=product_code,
        variant=variant,
        amount=Money.of(amount),
        term_months=term_months,
        owner_guarantee=owner_guarantee,
        collateral=collateral,
        consents=consents,
        statements=statements,
        created_at=NOW,
    )


def clean_report(subject_inn: str, **overrides: Any) -> CreditReport:
    base = CreditReport(
        subject_inn=subject_inn,
        has_history=True,
        current_overdue_days=0,
        max_closed_overdue_days_last_year=0,
        monthly_payments=Money.zero(),
        has_closed_loans_without_overdue=True,
        inquiries_last_3_months=0,
    )
    return replace(base, **overrides)


def clean_tax(inn: str = COMPANY_INN, **overrides: Any) -> TaxInfo:
    base = TaxInfo(
        inn=inn,
        accounts_blocked=False,
        mass_address=False,
        mass_director=False,
        mass_founder=False,
        director_disqualified=False,
        tax_debt=Money.zero(),
        data_date=date(2026, 10, 1),
    )
    return replace(base, **overrides)


def no_bailiff(subject_inn: str) -> BailiffInfo:
    return BailiffInfo(subject_inn=subject_inn, open_proceedings=0, total_debt=Money.zero())


def make_turnover(
    inn: str = COMPANY_INN, monthly: str = "2000000", months: int = 12
) -> MonthlyTurnover:
    first = date(2025, 10, 1)
    return MonthlyTurnover(
        inn=inn,
        months=tuple(
            MonthRevenue(month=add_months(first, i), incoming=Money.of(monthly))
            for i in range(months)
        ),
    )


def make_statement(
    *,
    inn: str = COMPANY_INN,
    date_from: date = date(2025, 10, 24),
    date_to: date = date(2026, 10, 23),
    last_operation: date | None = None,
    monthly_incoming: str = "1000000",
    signature: SignatureStatus = SignatureStatus.VALID,
) -> BankStatement:
    """По поступлению в месяц, последнее в день last_operation (по умолчанию конец периода)."""
    last = last_operation or date_to
    period = Period(date_from, date_to)
    operations = tuple(
        StatementOperation(
            date=add_months(last, -i),
            amount=Money.of(monthly_incoming),
            direction=Direction.INCOMING,
            counterparty_inn="7702987655",
        )
        for i in range(period.months())
    )
    return BankStatement(
        id=uuid4(),
        file_name="statement.txt",
        file_key="statements/statement.txt",
        bank_name="Другой банк",
        account_inn=inn,
        period=period,
        signature_status=signature,
        operations=operations,
        uploaded_at=NOW,
    )


def make_context(
    *,
    application: Application | None = None,
    company: CompanyProfile | None = None,
    **overrides: Any,
) -> PipelineContext:
    """Здоровое ООО без поводов для отказа и ручной проверки; тест меняет то, что проверяет."""
    company = company or make_company()
    application = application or make_application(inn=company.inn, kind=company.kind)
    fields: dict[str, Any] = {
        "application": application,
        "company": company,
        "credit_report": clean_report(company.inn),
        "related_credit_reports": tuple(
            clean_report(inn)
            for inn in sorted(
                {company.director_inn, *(f.inn for f in company.founders)} - {company.inn}
            )
        ),
        "turnover": make_turnover(company.inn),
        "statement": None,
        "tax": clean_tax(company.inn),
        "bailiff": no_bailiff(company.inn),
        "director_bailiff": no_bailiff(company.director_inn)
        if company.kind is CompanyKind.LLC
        else None,
        "cbr_level": KycLevel.GREEN,
        "rosfin_hit": False,
        "reported_annual_revenue": None,
        "statement_verification": None,
        "now": NOW,
    }
    fields.update(overrides)
    return PipelineContext(**fields)


# --- Контекст из синтетики этапа 2 ---


def bki_consent() -> Consent:
    return Consent(type=ConsentType.BKI_COMPANY, given_by=uuid4(), given_at=NOW)


def statement_from_raw(raw: bytes, signature: SignatureStatus) -> BankStatement:
    parsed = Statement1CParser().parse(raw)
    return BankStatement(
        id=uuid4(),
        file_name="statement.txt",
        file_key="statements/statement.txt",
        bank_name=parsed.bank_name,
        account_inn=parsed.account_inn,
        period=parsed.period,
        signature_status=signature,
        operations=parsed.operations,
        uploaded_at=datetime(2026, 10, 24, 8, 0, tzinfo=UTC),
    )


async def collect_context(
    fixtures: SyntheticData, application: Application, *, statement_raw: bytes | None = None
) -> PipelineContext:
    """То же, что сделает DataCollector этапа 5, но прямо через моки, без повторов и снимков."""
    failures = MockFailures()
    inn = application.inn
    consent = bki_consent()

    company = await MockEgrulAdapter(fixtures, failures).find_by_inn(inn)
    bki = MockBkiAdapter(fixtures, failures)
    persons = sorted({company.director_inn, *(f.inn for f in company.founders)} - {inn})
    fssp = MockFsspAdapter(fixtures, failures)
    fns = MockFnsAdapter(fixtures, failures)

    statement = None
    if statement_raw is not None:
        signature = await MockSignatureAdapter(fixtures, failures).verify(statement_raw)
        statement = statement_from_raw(statement_raw, signature)

    ctx = PipelineContext(
        application=application,
        company=company,
        credit_report=await bki.report(inn, consent),
        related_credit_reports=tuple([await bki.report(p, consent) for p in persons]),
        turnover=await MockTurnoverAdapter(fixtures, failures).monthly_turnover(inn, 12),
        statement=statement,
        tax=await fns.tax_info(inn),
        bailiff=await fssp.open_proceedings(inn),
        director_bailiff=(
            await fssp.open_proceedings(company.director_inn)
            if company.kind is CompanyKind.LLC
            else None
        ),
        cbr_level=await MockCbrAdapter(fixtures, failures).risk_level(inn),
        rosfin_hit=await MockRosfinAdapter(fixtures, failures).is_listed(
            inn, [company.director, *(f.full_name for f in company.founders)]
        ),
        reported_annual_revenue=await fns.annual_revenue(inn, TODAY.year - 1),
        now=NOW,
    )
    if statement is None:
        return ctx
    verifier = StatementVerifier(default_authenticity_strategies())
    return replace(ctx, statement_verification=verifier.verify(statement, ctx))
