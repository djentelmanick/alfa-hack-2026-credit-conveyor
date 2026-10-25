"""Есть ли у заявителя полномочия действовать от имени компании (SCOPE, раздел 3)."""

from dataclasses import dataclass
from typing import Self

from app.domain.authority import (
    ApplicantAuthority,
    PoaRequest,
    PowerOfAttorney,
    check_power_of_attorney,
)
from app.domain.entities import User, UserCompany
from app.domain.enums import ApplicantRole, AuthorityBasis, CompanyKind, PoaKind
from app.domain.errors import FieldError, NotDirector, ValidationFailed
from app.domain.ports import Clock, MchdRegistryPort, NotaryPoaRegistryPort
from app.domain.value_objects import CompanyProfile


@dataclass(frozen=True, kw_only=True, slots=True)
class AuthorityClaim:
    """Что заявитель говорит о себе: роль и, если он не директор, доверенность."""

    role: ApplicantRole
    poa: PoaRequest | None = None

    @classmethod
    def from_link(cls, link: UserCompany | None) -> Self | None:
        if link is None or link.authority is None:
            return None
        return cls(role=link.authority.role, poa=link.authority.poa)


class AuthorityService:
    def __init__(
        self, *, mchd: MchdRegistryPort, notary: NotaryPoaRegistryPort, clock: Clock
    ) -> None:
        self._mchd = mchd
        self._notary = notary
        self._clock = clock

    async def resolve(
        self, applicant: User, company: CompanyProfile, claim: AuthorityClaim | None
    ) -> ApplicantAuthority:
        """Бросает NotDirector без оснований и ValidationFailed с причинами отказа доверенности."""
        if company.is_director(applicant):
            is_ie = company.kind is CompanyKind.IE
            # У директора полномочия есть по закону, поэтому выбранная им роль и
            # приложенная доверенность ничего не меняют.
            return ApplicantAuthority(
                role=ApplicantRole.SOLE_TRADER if is_ie else ApplicantRole.DIRECTOR,
                basis=AuthorityBasis.SOLE_TRADER if is_ie else AuthorityBasis.DIRECTOR,
                representative_inn=applicant.inn or "",
                representative_name=applicant.full_name,
            )
        if company.kind is CompanyKind.IE or applicant.inn is None:
            raise NotDirector()
        if claim is None or claim.poa is None:
            raise NotDirector()
        if claim.role is ApplicantRole.DIRECTOR:
            raise ValidationFailed(
                [
                    FieldError(
                        "authority.role",
                        "NOT_DIRECTOR_BY_EGRUL",
                        f"По ЕГРЮЛ руководитель {company.director}. "
                        "Выберите свою роль и приложите доверенность",
                    )
                ]
            )
        poa = await self._find(claim.poa, company, applicant.inn)
        errors = check_power_of_attorney(
            poa, company=company, representative_inn=applicant.inn, today=self._clock.today()
        )
        if errors:
            raise ValidationFailed(list(errors))
        return ApplicantAuthority(
            role=claim.role,
            basis=AuthorityBasis.POWER_OF_ATTORNEY,
            representative_inn=applicant.inn,
            representative_name=applicant.full_name,
            poa=claim.poa,
            registered_founder=(
                company.is_founder(applicant) if claim.role is ApplicantRole.OWNER else None
            ),
        )

    async def _find(
        self, request: PoaRequest, company: CompanyProfile, representative_inn: str
    ) -> PowerOfAttorney | None:
        if request.kind is PoaKind.MCHD:
            if request.mchd_guid is None:
                return None
            return await self._mchd.find(request.mchd_guid, company.inn, representative_inn)
        if request.notarized_on is None or not request.register_number or not request.notary_name:
            return None
        return await self._notary.find(
            request.notarized_on, request.register_number, request.notary_name
        )
