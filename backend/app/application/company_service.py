from collections.abc import Callable
from dataclasses import dataclass

from app.domain.entities import TokenClaims
from app.domain.enums import Role
from app.domain.errors import NotDirector
from app.domain.ports import AccountTurnoverPort, CompanyRegistryPort, UnitOfWork
from app.domain.value_objects import CompanyProfile, Money

TURNOVER_MONTHS = 12
ANALYST_CANNOT_APPLY = "Заявку подаёт клиент"


@dataclass(frozen=True, kw_only=True, slots=True)
class CompanyView:
    """Банковские данные (has_account, annual_revenue) есть только у руководителя и аналитика."""

    profile: CompanyProfile
    has_account: bool | None
    annual_revenue: Money | None
    can_apply: bool
    cannot_apply_reason: str | None


class CompanyService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        registry: CompanyRegistryPort,
        turnover: AccountTurnoverPort,
    ) -> None:
        self._uow = uow
        self._registry = registry
        self._turnover = turnover

    async def get(self, claims: TokenClaims, inn: str) -> CompanyView:
        profile = await self._registry.find_by_inn(inn)
        reason: str | None
        if claims.role is Role.ANALYST:
            can_apply, reason, sees_bank_data = False, ANALYST_CANNOT_APPLY, True
        else:
            async with self._uow() as uow:
                user = await uow.users.get(claims.user_id)
            is_director = profile.is_director(user)
            reason = None if is_director else NotDirector.default_message
            can_apply, sees_bank_data = is_director, is_director

        has_account: bool | None = None
        annual_revenue: Money | None = None
        if sees_bank_data:
            turnover = await self._turnover.monthly_turnover(inn, TURNOVER_MONTHS)
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
        )
