from collections.abc import Callable
from dataclasses import dataclass

from app.application.authority_service import AuthorityClaim, AuthorityService
from app.domain.authority import ApplicantAuthority
from app.domain.entities import TokenClaims, User, UserCompany
from app.domain.enums import AuthorityBasis, CompanyKind, Role
from app.domain.errors import NotDirector, ValidationFailed
from app.domain.ports import AccountTurnoverPort, CompanyRegistryPort, UnitOfWork
from app.domain.value_objects import CompanyProfile, Money

TURNOVER_MONTHS = 12
ANALYST_CANNOT_APPLY = "Заявку подаёт клиент"


@dataclass(frozen=True, kw_only=True, slots=True)
class CompanyView:
    """Банковские данные (has_account, annual_revenue) видят только те, кто может подать заявку,
    и аналитик."""

    profile: CompanyProfile
    has_account: bool | None
    annual_revenue: Money | None
    can_apply: bool
    cannot_apply_reason: str | None
    authority_required: bool = False
    authority: ApplicantAuthority | None = None


class CompanyService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        registry: CompanyRegistryPort,
        turnover: AccountTurnoverPort,
        authority: AuthorityService,
    ) -> None:
        self._uow = uow
        self._registry = registry
        self._turnover = turnover
        self._authority = authority

    async def get(self, claims: TokenClaims, inn: str) -> CompanyView:
        profile = await self._registry.find_by_inn(inn)
        if claims.role is Role.ANALYST:
            return await self._view(
                profile, can_apply=False, reason=ANALYST_CANNOT_APPLY, sees=True
            )
        async with self._uow() as uow:
            user = await uow.users.get(claims.user_id)
        return await self._client_view(user, profile)

    async def claim_authority(
        self, claims: TokenClaims, inn: str, claim: AuthorityClaim
    ) -> CompanyView:
        """Проверяет доверенность и привязывает компанию к представителю."""
        profile = await self._registry.find_by_inn(inn)
        async with self._uow() as uow:
            user = await uow.users.get(claims.user_id)
            authority = await self._authority.resolve(user, profile, claim)
            user.link_company(
                UserCompany(
                    inn=profile.inn,
                    name=profile.name,
                    kind=profile.kind,
                    is_director=authority.basis is not AuthorityBasis.POWER_OF_ATTORNEY,
                    authority=authority,
                )
            )
            await uow.commit()
        return await self._client_view(user, profile)

    async def _client_view(self, user: User, profile: CompanyProfile) -> CompanyView:
        authority: ApplicantAuthority | None = None
        reason = NotDirector.default_message
        try:
            # Привязка по доверенности перепроверяется: её могли отозвать или она истекла,
            # и тогда представитель снова видит шаг с доверенностью, а не банковские данные.
            authority = await self._authority.resolve(
                user, profile, AuthorityClaim.from_link(user.company(profile.inn))
            )
        except NotDirector:
            pass
        except ValidationFailed as exc:
            reason = "; ".join(e.message for e in exc.details)
        can_apply = authority is not None
        return await self._view(
            profile,
            can_apply=can_apply,
            reason=None if can_apply else reason,
            sees=can_apply,
            authority_required=not can_apply and profile.kind is CompanyKind.LLC,
            authority=authority,
        )

    async def _view(
        self,
        profile: CompanyProfile,
        *,
        can_apply: bool,
        reason: str | None,
        sees: bool,
        authority_required: bool = False,
        authority: ApplicantAuthority | None = None,
    ) -> CompanyView:
        has_account: bool | None = None
        annual_revenue: Money | None = None
        if sees:
            turnover = await self._turnover.monthly_turnover(profile.inn, TURNOVER_MONTHS)
            has_account = turnover is not None
            if turnover is not None and turnover.months:
                # Счёт мог открыться меньше года назад, тогда выручку приводим к году.
                annual_revenue = turnover.total() * 12 / len(turnover.months)
        return CompanyView(
            profile=profile,
            has_account=has_account,
            annual_revenue=annual_revenue,
            can_apply=can_apply,
            cannot_apply_reason=reason,
            authority_required=authority_required,
            authority=authority,
        )
