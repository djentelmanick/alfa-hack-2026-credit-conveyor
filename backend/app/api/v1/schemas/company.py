from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.api.v1.schemas.common import INN_PATTERN, MoneyStr, Percent, RequestModel
from app.application.authority_service import AuthorityClaim
from app.application.company_service import CompanyView
from app.domain.authority import ApplicantAuthority, PoaRequest
from app.domain.entities import Consent
from app.domain.enums import (
    ApplicantRole,
    AuthorityBasis,
    CompanyKind,
    CompanyStatus,
    ConsentType,
    PoaKind,
)


class InnIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)


class ConsentGiveIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)
    types: list[ConsentType] = Field(min_length=1)


class PoaIn(RequestModel):
    kind: PoaKind
    mchd_guid: UUID | None = None
    notarized_on: date | None = None
    register_number: str | None = Field(default=None, min_length=1, max_length=64)
    notary_name: str | None = Field(default=None, min_length=3, max_length=255)

    @model_validator(mode="after")
    def _requisites_for_kind(self) -> Self:
        if self.kind is PoaKind.MCHD and self.mchd_guid is None:
            raise ValueError("Для МЧД нужен её номер (GUID)")
        if self.kind is PoaKind.NOTARIAL and not (
            self.notarized_on and self.register_number and self.notary_name
        ):
            raise ValueError(
                "Для нотариальной доверенности нужны дата, реестровый номер и нотариус"
            )
        return self

    def to_domain(self) -> PoaRequest:
        return PoaRequest(
            kind=self.kind,
            mchd_guid=self.mchd_guid if self.kind is PoaKind.MCHD else None,
            notarized_on=self.notarized_on if self.kind is PoaKind.NOTARIAL else None,
            register_number=self.register_number if self.kind is PoaKind.NOTARIAL else None,
            notary_name=self.notary_name if self.kind is PoaKind.NOTARIAL else None,
        )


class AuthorityIn(RequestModel):
    inn: str = Field(pattern=INN_PATTERN)
    role: ApplicantRole
    power_of_attorney: PoaIn | None = None

    def to_claim(self) -> AuthorityClaim:
        poa = self.power_of_attorney
        return AuthorityClaim(role=self.role, poa=poa.to_domain() if poa else None)


class AuthorityOut(BaseModel):
    role: ApplicantRole
    basis: AuthorityBasis
    representative_name: str
    power_of_attorney: str | None
    registered_founder: bool | None

    @classmethod
    def from_domain(cls, a: ApplicantAuthority) -> Self:
        return cls(
            role=a.role,
            basis=a.basis,
            representative_name=a.representative_name,
            power_of_attorney=a.poa.describe() if a.poa else None,
            registered_founder=a.registered_founder,
        )


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
    authority_required: bool
    authority: AuthorityOut | None = None

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
            authority_required=view.authority_required,
            authority=AuthorityOut.from_domain(view.authority) if view.authority else None,
        )


class ConsentOut(BaseModel):
    type: ConsentType
    given_by: UUID
    given_at: datetime

    @classmethod
    def from_domain(cls, consent: Consent) -> Self:
        return cls(type=consent.type, given_by=consent.given_by, given_at=consent.given_at)
