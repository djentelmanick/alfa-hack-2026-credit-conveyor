from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.application import (
    ApplicationOut,
    CollateralOut,
    FactorScoreOut,
    StatementOut,
    StatusTransitionOut,
)
from app.api.v1.schemas.common import INN_PATTERN, MoneyStr, Percent, RateStr, RequestModel
from app.api.v1.schemas.company import ConsentOut
from app.api.v1.schemas.labels import check_title
from app.application.analyst_service import AnalystListEntry, AnalystPage, OfferTerms
from app.application.application_service import ApplicationService
from app.application.report_builder import ApplicationReport
from app.domain.entities import TokenClaims
from app.domain.enums import (
    ApplicationStatus,
    CheckOutcome,
    CheckStage,
    CompanyKind,
    DecisionOutcome,
    FetchStatus,
    ProductCode,
    RiskClass,
    SourceType,
    Variant,
)
from app.domain.ports import AnalystFilter
from app.domain.value_objects import Money

CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"
COMMENT_MAX_LENGTH = 2000


class AnalystListIn(RequestModel):
    queue: Literal["manual"] | None = None
    status: ApplicationStatus | None = None
    inn: str | None = Field(default=None, pattern=INN_PATTERN)
    risk_class: RiskClass | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)

    def to_filter(self) -> AnalystFilter:
        return AnalystFilter(
            manual_queue=self.queue == "manual",
            status=self.status,
            inn=self.inn,
            risk_class=self.risk_class,
        )


class _AnalystAction(RequestModel):
    application_id: UUID
    # Пустую строку и строку из пробелов отсекает strip в RequestModel и min_length.
    comment: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)


class ApproveIn(_AnalystAction):
    rate: Decimal = Field(gt=0, lt=100, decimal_places=2)


class OfferOptionIn(RequestModel):
    product_code: ProductCode
    variant: Variant
    amount: Decimal = Field(gt=0, decimal_places=2)
    term_months: int = Field(gt=0)
    rate: Decimal = Field(gt=0, lt=100, decimal_places=2)

    def to_terms(self) -> OfferTerms:
        return OfferTerms(
            product_code=self.product_code,
            variant=self.variant,
            amount=Money.of(self.amount),
            term_months=self.term_months,
            rate=self.rate,
        )


class CounterOfferIn(_AnalystAction):
    options: list[OfferOptionIn] = Field(min_length=1, max_length=5)


class InfoItemIn(RequestModel):
    code: str = Field(pattern=CODE_PATTERN, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class RequestInfoIn(_AnalystAction):
    items: list[InfoItemIn] = Field(min_length=1, max_length=10)

    def to_items(self) -> dict[str, str]:
        return {item.code: item.message for item in self.items}


class RejectIn(_AnalystAction):
    reason_code: str = Field(pattern=CODE_PATTERN, max_length=64)


class RecheckIn(_AnalystAction):
    pass


class RecheckOut(BaseModel):
    application_id: UUID
    status: Literal["CHECKING"]


class AnalystListItemOut(BaseModel):
    id: UUID
    inn: str
    company_name: str
    product_code: ProductCode
    variant: Variant
    amount: MoneyStr
    status: ApplicationStatus
    risk_class: RiskClass | None = None
    manual_reasons: list[str]
    submitted_at: datetime

    @classmethod
    def from_entry(cls, entry: AnalystListEntry) -> Self:
        a = entry.application
        return cls(
            id=a.id,
            inn=a.inn,
            company_name=a.company_name,
            product_code=ProductCode(a.product_code),
            variant=a.variant,
            amount=a.amount.amount,
            status=a.status,
            risk_class=a.scoring.risk_class if a.scoring else None,
            manual_reasons=list(entry.manual_reasons),
            submitted_at=entry.submitted_at,
        )


class AnalystPageOut(BaseModel):
    items: list[AnalystListItemOut]
    total: int
    page: int
    size: int

    @classmethod
    def from_domain(cls, page: AnalystPage) -> Self:
        return cls(
            items=[AnalystListItemOut.from_entry(e) for e in page.items],
            total=page.total,
            page=page.page,
            size=page.size,
        )


class ApplicantOut(BaseModel):
    full_name: str
    passport_verified: bool


class ProvidedOut(BaseModel):
    applicant: ApplicantOut
    consents: list[ConsentOut]
    collateral: CollateralOut | None = None
    owner_guarantee_consent: bool | None = None
    statements: list[StatementOut]


class SourceOut(BaseModel):
    source: SourceType
    status: FetchStatus
    attempts: int
    fetched_at: datetime | None = None
    data: dict[str, Any] | None = None


class ComparisonOut(BaseModel):
    code: str
    title: str
    stage: CheckStage
    expected: str
    actual: str
    outcome: CheckOutcome


class TurnoverMonthOut(BaseModel):
    month: str
    incoming: MoneyStr
    outgoing: MoneyStr


class TopPayerOut(BaseModel):
    inn: str
    name: str
    share_pct: Percent


class ReportScoringOut(BaseModel):
    total: int
    risk_class: RiskClass
    debt_load_pct: Percent
    factors: list[FactorScoreOut]


class AnalystDecisionOut(BaseModel):
    id: UUID
    name: str
    rate: RateStr | None = None
    comment: str
    decided_at: datetime


class ReportDecisionOut(BaseModel):
    rule_code: str
    outcome: DecisionOutcome
    manual_reasons: list[str]
    system_rate: RateStr | None = None
    analyst: AnalystDecisionOut | None = None


class ReportContractOut(BaseModel):
    id: UUID
    number: str
    versions_count: int


class ApplicationReportOut(BaseModel):
    application: ApplicationOut
    provided: ProvidedOut
    sources: list[SourceOut]
    comparisons: list[ComparisonOut]
    turnover: list[TurnoverMonthOut]
    top_payers: list[TopPayerOut]
    scoring: ReportScoringOut | None = None
    decision: ReportDecisionOut | None = None
    history: list[StatusTransitionOut]
    contract: ReportContractOut | None = None

    @classmethod
    def from_report(cls, report: ApplicationReport, claims: TokenClaims) -> Self:
        a = report.application
        scoring = report.scoring
        decision = report.decision
        analyst = decision.analyst if decision else None
        return cls(
            application=ApplicationOut.from_view(ApplicationService.view(a, claims)),
            provided=ProvidedOut(
                applicant=ApplicantOut(
                    full_name=report.applicant.full_name,
                    passport_verified=report.applicant.passport_verified,
                ),
                consents=[ConsentOut.from_domain(c) for c in a.consents],
                collateral=CollateralOut.from_domain(a.collateral) if a.collateral else None,
                owner_guarantee_consent=(
                    a.owner_guarantee if a.company_kind is CompanyKind.LLC else None
                ),
                statements=[StatementOut.from_domain(s, a.inn) for s in a.statements],
            ),
            sources=[
                SourceOut(
                    source=s.source,
                    status=s.status,
                    attempts=s.attempts,
                    fetched_at=s.fetched_at,
                    data=dict(s.data) or None,
                )
                for s in report.sources
            ],
            comparisons=[
                ComparisonOut(
                    code=c.code,
                    title=check_title(c.code),
                    stage=c.stage,
                    expected=c.expected,
                    actual=c.actual,
                    outcome=c.outcome,
                )
                for c in report.comparisons
            ],
            turnover=[
                TurnoverMonthOut(
                    month=m.month.strftime("%Y-%m"),
                    incoming=m.incoming.amount,
                    outgoing=m.outgoing.amount,
                )
                for m in report.turnover
            ],
            top_payers=[
                TopPayerOut(inn=p.inn, name=p.name, share_pct=p.share_pct)
                for p in report.top_payers
            ],
            scoring=(
                ReportScoringOut(
                    total=scoring.total,
                    risk_class=scoring.risk_class,
                    debt_load_pct=Decimal(str(scoring.debt_load * 100)).quantize(Decimal("0.1")),
                    factors=[
                        FactorScoreOut(code=f.code, label=f.label, value=f.value, points=f.points)
                        for f in scoring.factors
                    ],
                )
                if scoring
                else None
            ),
            decision=(
                ReportDecisionOut(
                    rule_code=decision.rule_code,
                    outcome=decision.outcome,
                    manual_reasons=list(decision.manual_reasons),
                    system_rate=decision.system_rate,
                    analyst=(
                        AnalystDecisionOut(
                            id=analyst.actor_id,
                            name=analyst.actor_name,
                            rate=analyst.rate,
                            comment=analyst.comment,
                            decided_at=analyst.decided_at,
                        )
                        if analyst
                        else None
                    ),
                )
                if decision
                else None
            ),
            history=[StatusTransitionOut.from_entry(e) for e in report.history],
            contract=(
                ReportContractOut(
                    id=report.contract.id,
                    number=report.contract.number,
                    versions_count=report.contract.versions_count,
                )
                if report.contract
                else None
            ),
        )
