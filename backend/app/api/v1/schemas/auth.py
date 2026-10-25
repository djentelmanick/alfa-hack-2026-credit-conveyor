from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.common import RequestModel
from app.application.auth_service import AccessToken, Passport, Registration
from app.domain.entities import User
from app.domain.enums import AuthorityBasis, CompanyKind, Role


class PassportIn(RequestModel):
    series: str = Field(pattern=r"^[0-9]{4}$")
    number: str = Field(pattern=r"^[0-9]{6}$")
    issued_at: date


class RegisterIn(RequestModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=3, max_length=255)
    inn: str = Field(pattern=r"^[0-9]{12}$")
    passport: PassportIn

    def to_registration(self) -> Registration:
        return Registration(
            login=self.login,
            password=self.password,
            full_name=self.full_name,
            inn=self.inn,
            passport=Passport(
                series=self.passport.series,
                number=self.passport.number,
                issued_at=self.passport.issued_at,
            ),
        )


class LoginIn(RequestModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserCompanyOut(BaseModel):
    inn: str
    name: str
    kind: CompanyKind
    is_director: bool
    basis: AuthorityBasis | None = None


class UserOut(BaseModel):
    id: UUID
    role: Role
    full_name: str
    inn: str | None
    passport_verified: bool
    companies: list[UserCompanyOut]

    @classmethod
    def from_domain(cls, user: User) -> Self:
        return cls(
            id=user.id,
            role=user.role,
            full_name=user.full_name,
            inn=user.inn,
            passport_verified=user.passport_verified,
            companies=[
                UserCompanyOut(
                    inn=c.inn,
                    name=c.name,
                    kind=c.kind,
                    is_director=c.is_director,
                    basis=c.authority.basis if c.authority else None,
                )
                for c in user.companies
            ],
        )


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
    user: UserOut

    @classmethod
    def from_domain(cls, token: AccessToken) -> Self:
        return cls(
            access_token=token.access_token,
            token_type="bearer",  # noqa: S106 это схема авторизации, а не пароль
            expires_in=token.expires_in,
            user=UserOut.from_domain(token.user),
        )
