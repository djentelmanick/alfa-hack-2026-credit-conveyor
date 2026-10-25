from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.common import INN_PATTERN, MoneyStr, Percent, RequestModel
from app.application.company_service import CompanyView
from app.domain.entities import Consent
from app.domain.enums import CompanyKind, CompanyStatus, ConsentType


class InnIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)


class ConsentGiveIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)
    types: list[ConsentType] = Field(min_length=1)


class OkvedOut(BaseModel):
    code: str
    label: str


class FounderOut(BaseModel):
    name: str
    share_pct: Percent


class CompanyOut(BaseModel):
    inn: str
    kind: CompanyKind
    name: str
    ogrn: str
    registered_at: date
    status: CompanyStatus
    okved: OkvedOut
    director: str
    founders: list[FounderOut]
    address: str
    has_account: bool | None = None
    annual_revenue: MoneyStr | None = None
    can_apply: bool
    cannot_apply_reason: str | None = None

    @classmethod
    def from_domain(cls, view: CompanyView) -> Self:
        p = view.profile
        return cls(
            inn=p.inn,
            kind=p.kind,
            name=p.name,
            ogrn=p.ogrn,
            registered_at=p.registered_at,
            status=p.status,
            okved=OkvedOut(code=p.okved, label=p.okved_label),
            director=p.director,
            founders=[FounderOut(name=f.full_name, share_pct=f.share_pct) for f in p.founders],
            address=p.address,
            has_account=view.has_account,
            annual_revenue=view.annual_revenue.amount if view.annual_revenue else None,
            can_apply=view.can_apply,
            cannot_apply_reason=view.cannot_apply_reason,
        )


class ConsentOut(BaseModel):
    type: ConsentType
    given_by: UUID
    given_at: datetime

    @classmethod
    def from_domain(cls, consent: Consent) -> Self:
        return cls(type=consent.type, given_by=consent.given_by, given_at=consent.given_at)
