from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.authority import ApplicantAuthority
from app.domain.enums import AuthorityBasis, CompanyKind, Role


@dataclass(frozen=True, kw_only=True, slots=True)
class UserCompany:
    """Компания в кабинете пользователя.

    Название и форма запоминаются при привязке: вход и /auth/me не должны зависеть
    от доступности ЕГРЮЛ.
    """

    inn: str
    name: str
    kind: CompanyKind
    is_director: bool
    authority: ApplicantAuthority | None = None


@dataclass(kw_only=True, eq=False)
class User:
    id: UUID
    login: str
    password_hash: str
    role: Role
    full_name: str
    passport_verified: bool
    created_at: datetime
    inn: str | None = None
    companies: list[UserCompany] = field(default_factory=list)

    def company(self, inn: str) -> UserCompany | None:
        return next((c for c in self.companies if c.inn == inn), None)

    def represents(self, inn: str) -> bool:
        """Привязка по подтверждённой доверенности. Сама доверенность перепроверяется при подаче."""
        link = self.company(inn)
        return (
            link is not None
            and link.authority is not None
            and link.authority.basis is AuthorityBasis.POWER_OF_ATTORNEY
        )

    def link_company(self, company: UserCompany) -> None:
        """Повторная привязка обновляет сведения о компании, а не дублирует её."""
        self.companies = [c for c in self.companies if c.inn != company.inn]
        self.companies.append(company)


@dataclass(frozen=True, kw_only=True, slots=True)
class TokenClaims:
    """Расшифрованный JWT. Единственный источник id и роли пользователя в запросе."""

    user_id: UUID
    role: Role
    name: str
    issued_at: datetime
    expires_at: datetime
    token_id: str
