from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.enums import CompanyKind, Role


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


@dataclass(kw_only=True, eq=False)
class User:
    id: UUID
    login: str
    password_hash: str
    role: Role
    full_name: str
    passport_verified: bool
    created_at: datetime
    companies: list[UserCompany] = field(default_factory=list)

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
