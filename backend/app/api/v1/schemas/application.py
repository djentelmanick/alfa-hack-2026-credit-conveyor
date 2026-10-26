from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.common import INN_PATTERN, MoneyStr, Percent, RateStr, RequestModel
from app.api.v1.schemas.company import AuthorityOut, OkvedOut
from app.api.v1.schemas.labels import INFO_ITEM_LABELS, STATUS_LABELS, reason_label
from app.application.application_service import (
    UNSET,
    ApplicationPage,
    ClientApplicationView,
    DecisionView,
    FormPatch,
    HistoryEntry,
    PresignedUrl,
)
from app.application.findings_builder import ApplicationFindings
from app.domain.entities import Application, ApplicationForm, BankStatement, InfoRequest
from app.domain.enums import (
    AllowedAction,
    ApplicationStatus,
    CollateralType,
    CompanyKind,
    CompanyStatus,
    DecisionOutcome,
    ProductCode,
    Purpose,
    Role,
    SignatureStatus,
    Variant,
)
from app.domain.errors import FieldError
from app.domain.value_objects import Collateral, Money


class CollateralIn(RequestModel):
    type: CollateralType
    description: str = Field(min_length=1, max_length=1000)
    estimated_value: Decimal = Field(gt=0, decimal_places=2)

    def to_domain(self) -> Collateral:
        return Collateral(
            type=self.type,
            description=self.description,
            estimated_value=Money.of(self.estimated_value),
        )


class ApplicationCreateIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)
    purpose: Purpose
    product_code: ProductCode
    variant: Variant
    amount: Decimal = Field(gt=0, decimal_places=2)
    term_months: int = Field(gt=0)
    collateral: CollateralIn | None = None
    owner_guarantee_consent: bool = False

    def to_form(self) -> ApplicationForm:
        return ApplicationForm(
            purpose=self.purpose,
            product_code=self.product_code,
            variant=self.variant,
            amount=Money.of(self.amount),
            term_months=self.term_months,
            owner_guarantee=self.owner_guarantee_consent,
            collateral=self.collateral.to_domain() if self.collateral else None,
        )


class ApplicationIdIn(RequestModel):
    application_id: UUID


class StatementIdIn(RequestModel):
    application_id: UUID
    statement_id: UUID


class FileUrlOut(BaseModel):
    url: str
    file_name: str
    expires_at: datetime
    version_no: int | None = None

    @classmethod
    def from_domain(cls, link: PresignedUrl) -> Self:
        return cls(
            url=link.url,
            file_name=link.file_name,
            expires_at=link.expires_at,
            version_no=link.version_no,
        )


class ApplicationUpdateIn(RequestModel):
    application_id: UUID
    purpose: Purpose | None = None
    product_code: ProductCode | None = None
    variant: Variant | None = None
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    term_months: int | None = Field(default=None, gt=0)
    collateral: CollateralIn | None = None
    owner_guarantee_consent: bool | None = None

    def to_patch(self) -> FormPatch:
        # Явный null в collateral убирает залог, отсутствие поля оставляет прежний.
        collateral = (
            (self.collateral.to_domain() if self.collateral else None)
            if "collateral" in self.model_fields_set
            else UNSET
        )
        return FormPatch(
            purpose=self.purpose,
            product_code=self.product_code,
            variant=self.variant,
            amount=Money.of(self.amount) if self.amount is not None else None,
            term_months=self.term_months,
            owner_guarantee=self.owner_guarantee_consent,
            collateral=collateral,
        )


class ApplicationListIn(RequestModel):
    status: ApplicationStatus | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class AcceptIn(RequestModel):
    application_id: UUID
    offer_id: UUID | None = None


class CancelIn(RequestModel):
    application_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class CompanyBriefOut(BaseModel):
    name: str
    kind: CompanyKind
    okved: str


class CollateralOut(BaseModel):
    type: CollateralType
    description: str
    estimated_value: MoneyStr

    @classmethod
    def from_domain(cls, c: Collateral) -> Self:
        return cls(type=c.type, description=c.description, estimated_value=c.estimated_value.amount)


class StatementOut(BaseModel):
    id: UUID
    file_name: str
    bank_name: str
    account_inn: str
    inn_matches: bool
    period_from: date
    period_to: date
    months_covered: int
    last_operation_at: date | None = None
    operations_count: int
    signature_status: SignatureStatus
    annual_revenue: MoneyStr

    @classmethod
    def from_domain(cls, s: BankStatement, inn: str) -> Self:
        return cls(
            id=s.id,
            file_name=s.file_name,
            bank_name=s.bank_name,
            account_inn=s.account_inn,
            inn_matches=s.account_inn == inn,
            period_from=s.period.date_from,
            period_to=s.period.date_to,
            months_covered=s.months_covered(),
            last_operation_at=s.last_operation_date(),
            operations_count=len(s.operations),
            signature_status=s.signature_status,
            annual_revenue=s.annual_revenue().amount,
        )


class InfoItemOut(BaseModel):
    code: str
    message: str


class InfoRequestOut(BaseModel):
    items: list[InfoItemOut]
    message: str
    created_at: datetime

    @classmethod
    def from_domain(cls, r: InfoRequest) -> Self:
        return cls(
            items=[
                InfoItemOut(code=i, message=r.item_messages.get(i) or INFO_ITEM_LABELS.get(i, i))
                for i in r.items
            ],
            message=r.message,
            created_at=r.created_at,
        )


class FactorScoreOut(BaseModel):
    code: str
    label: str
    value: str
    points: int


class OfferOptionOut(BaseModel):
    id: UUID
    product_code: ProductCode
    variant: Variant
    amount: MoneyStr
    term_months: int
    rate: RateStr


class ClientDecisionOut(BaseModel):
    """Решение для клиента: исход и причины текстом. Баллы и класс риска видит только аналитик."""

    outcome: DecisionOutcome
    reasons: list[str]
    rate: RateStr | None = None
    offers: list[OfferOptionOut]
    expires_at: datetime | None = None

    @classmethod
    def from_view(cls, view: DecisionView) -> Self:
        d = view.decision
        codes = d.reasons or (d.rule_code,)
        factors = [f for f in view.scoring.factors if f.client_visible] if view.scoring else []
        # Отказ по классу D называет факторы, снизившие балл: их текст даёт сама модель скоринга.
        explained = {f.code: f.explanation or f.label for f in factors}
        return cls(
            outcome=d.outcome,
            reasons=[explained.get(code) or reason_label(code) for code in codes],
            rate=d.rate,
            offers=[
                OfferOptionOut(
                    id=o.id,
                    product_code=ProductCode(o.product_code),
                    variant=o.variant,
                    amount=o.amount.amount,
                    term_months=o.term_months,
                    rate=o.rate,
                )
                for o in d.offers
            ],
            expires_at=view.expires_at,
        )


class ApplicationOut(BaseModel):
    id: UUID
    inn: str
    company: CompanyBriefOut
    purpose: Purpose
    product_code: ProductCode
    variant: Variant
    amount: MoneyStr
    term_months: int
    collateral: CollateralOut | None = None
    owner_guarantee_consent: bool | None = None
    authority: AuthorityOut | None = None
    statements: list[StatementOut]
    status: ApplicationStatus
    status_label: str
    info_request: InfoRequestOut | None = None
    decision: ClientDecisionOut | None = None
    expires_at: datetime | None = None
    allowed_actions: list[AllowedAction]
    contract_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: ClientApplicationView) -> Self:
        a = view.application
        decision = a.current_decision
        info = a.open_info_request
        return cls(
            id=a.id,
            inn=a.inn,
            company=CompanyBriefOut(name=a.company_name, kind=a.company_kind, okved=a.okved),
            purpose=a.purpose,
            product_code=ProductCode(a.product_code),
            variant=a.variant,
            amount=a.amount.amount,
            term_months=a.term_months,
            collateral=CollateralOut.from_domain(a.collateral) if a.collateral else None,
            owner_guarantee_consent=a.owner_guarantee
            if a.company_kind is CompanyKind.LLC
            else None,
            authority=AuthorityOut.from_domain(a.authority) if a.authority else None,
            statements=[StatementOut.from_domain(s, a.inn) for s in a.statements],
            status=a.status,
            status_label=STATUS_LABELS[a.status],
            info_request=InfoRequestOut.from_domain(info) if info else None,
            decision=(
                ClientDecisionOut.from_view(
                    DecisionView(decision=decision, scoring=a.scoring, expires_at=a.expires_at)
                )
                if decision
                else None
            ),
            expires_at=a.expires_at,
            allowed_actions=view.allowed_actions,
            contract_id=a.contract_id,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )


class SubmitOut(BaseModel):
    application_id: UUID
    status: Literal["SUBMITTED"]


class ApplicationListItemOut(BaseModel):
    id: UUID
    inn: str
    company_name: str
    product_code: ProductCode
    variant: Variant
    amount: MoneyStr
    status: ApplicationStatus
    updated_at: datetime

    @classmethod
    def from_domain(cls, a: Application) -> Self:
        return cls(
            id=a.id,
            inn=a.inn,
            company_name=a.company_name,
            product_code=ProductCode(a.product_code),
            variant=a.variant,
            amount=a.amount.amount,
            status=a.status,
            updated_at=a.updated_at,
        )


class ApplicationPageOut(BaseModel):
    items: list[ApplicationListItemOut]
    total: int
    page: int
    size: int

    @classmethod
    def from_domain(cls, page: ApplicationPage) -> Self:
        return cls(
            items=[ApplicationListItemOut.from_domain(a) for a in page.items],
            total=page.total,
            page=page.page,
            size=page.size,
        )


class ValidationErrorOut(BaseModel):
    field: str
    code: str
    message: str


class ValidateOut(BaseModel):
    valid: bool
    errors: list[ValidationErrorOut]

    @classmethod
    def from_errors(cls, errors: list[FieldError]) -> Self:
        return cls(
            valid=not errors,
            errors=[
                ValidationErrorOut(field=e.field, code=e.code, message=e.message) for e in errors
            ],
        )


class ActorOut(BaseModel):
    id: UUID | None
    role: Role | Literal["SYSTEM"]
    name: str


class StatusTransitionOut(BaseModel):
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    actor: ActorOut
    reason: str | None = None
    at: datetime

    @classmethod
    def from_entry(cls, entry: HistoryEntry) -> Self:
        t = entry.transition
        return cls(
            from_status=t.from_status,
            to_status=t.to_status,
            actor=ActorOut(
                id=t.actor_id,
                role=entry.actor_role or "SYSTEM",
                name=entry.actor_name,
            ),
            reason=t.reason or None,
            at=t.at,
        )


class FindingsFounderOut(BaseModel):
    full_name: str
    share_pct: Percent


class FindingsCompanyOut(BaseModel):
    inn: str
    kind: CompanyKind
    name: str
    ogrn: str
    registered_at: date
    status: CompanyStatus
    okved: OkvedOut
    address: str
    director: str
    founders: list[FindingsFounderOut]


class CreditLoadOut(BaseModel):
    has_history: bool
    monthly_payments: MoneyStr


class TurnoverMonthOut(BaseModel):
    month: str
    incoming: MoneyStr


class AccountTurnoverOut(BaseModel):
    months: list[TurnoverMonthOut]
    total_incoming: MoneyStr


class AnnualReportOut(BaseModel):
    year: int
    revenue: MoneyStr


class RevenueOut(BaseModel):
    account_turnover: AccountTurnoverOut | None = None
    annual_report: AnnualReportOut | None = None


class ApplicationFindingsOut(BaseModel):
    application_id: UUID
    collected_at: datetime
    company: FindingsCompanyOut | None = None
    credit_load: CreditLoadOut | None = None
    revenue: RevenueOut | None = None

    @classmethod
    def from_domain(cls, findings: ApplicationFindings) -> Self:
        company = findings.company
        credit = findings.credit_load
        turnover = findings.account_turnover
        report = findings.annual_report
        revenue = (
            RevenueOut(
                account_turnover=(
                    AccountTurnoverOut(
                        months=[
                            TurnoverMonthOut(
                                month=m.month.strftime("%Y-%m"), incoming=m.incoming.amount
                            )
                            for m in turnover.months
                        ],
                        total_incoming=turnover.total().amount,
                    )
                    if turnover is not None
                    else None
                ),
                annual_report=(
                    AnnualReportOut(year=report.year, revenue=report.revenue.amount)
                    if report is not None
                    else None
                ),
            )
            if turnover is not None or report is not None
            else None
        )
        return cls(
            application_id=findings.application_id,
            collected_at=findings.collected_at,
            company=(
                FindingsCompanyOut(
                    inn=company.inn,
                    kind=company.kind,
                    name=company.name,
                    ogrn=company.ogrn,
                    registered_at=company.registered_at,
                    status=company.status,
                    okved=OkvedOut(code=company.okved, label=company.okved_label),
                    address=company.address,
                    director=company.director,
                    founders=[
                        FindingsFounderOut(full_name=f.full_name, share_pct=f.share_pct)
                        for f in company.founders
                    ],
                )
                if company is not None
                else None
            ),
            credit_load=(
                CreditLoadOut(
                    has_history=credit.has_history,
                    monthly_payments=credit.monthly_payments.amount,
                )
                if credit is not None
                else None
            ),
            revenue=revenue,
        )
