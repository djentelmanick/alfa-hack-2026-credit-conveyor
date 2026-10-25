import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from app.domain.entities import TokenClaims, User
from app.domain.enums import Role
from app.domain.errors import (
    FieldError,
    InvalidCredentials,
    LoginTaken,
    NotFound,
    TokenInvalid,
    ValidationFailed,
)
from app.domain.ports import Clock, PassportCheckPort, PasswordHasher, TokenService, UnitOfWork


@dataclass(frozen=True, kw_only=True, slots=True)
class Passport:
    series: str
    number: str
    issued_at: date


@dataclass(frozen=True, kw_only=True, slots=True)
class Registration:
    login: str
    password: str
    full_name: str
    passport: Passport


@dataclass(frozen=True, kw_only=True, slots=True)
class AccessToken:
    access_token: str
    expires_in: int
    user: User


def normalize_login(login: str) -> str:
    return login.strip().casefold()


class AuthService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        hasher: PasswordHasher,
        tokens: TokenService,
        passports: PassportCheckPort,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._tokens = tokens
        self._passports = passports
        self._clock = clock
        self._dummy_hash: str | None = None

    async def register(self, data: Registration) -> User:
        """Сам клиент регистрируется только с ролью CLIENT, аналитиков заводит seed."""
        login = normalize_login(data.login)
        await self._check_passport(data.passport)
        async with self._uow() as uow:
            if await uow.users.get_by_login(login) is not None:
                raise LoginTaken()
            user = User(
                id=uuid4(),
                login=login,
                password_hash=await self._hasher.hash(data.password),
                role=Role.CLIENT,
                full_name=" ".join(data.full_name.split()),
                # Паспорт после проверки не храним: дальше нужен только факт, что он действителен.
                passport_verified=True,
                created_at=self._clock.now(),
            )
            await uow.users.add(user)
            await uow.commit()
        return user

    async def login(self, login: str, password: str) -> AccessToken:
        async with self._uow() as uow:
            user = await uow.users.get_by_login(normalize_login(login))
        if user is None:
            await self._hasher.verify(await self._timing_dummy_hash(), password)
            raise InvalidCredentials()
        if not await self._hasher.verify(user.password_hash, password):
            raise InvalidCredentials()
        return AccessToken(
            access_token=self._tokens.issue(user),
            expires_in=int(self._tokens.ttl.total_seconds()),
            user=user,
        )

    async def me(self, claims: TokenClaims) -> User:
        async with self._uow() as uow:
            try:
                return await uow.users.get(claims.user_id)
            except NotFound:
                # Подпись верна, но пользователя уже нет: токен больше ни к кому не относится.
                raise TokenInvalid() from None

    async def _check_passport(self, passport: Passport) -> None:
        if passport.issued_at > self._clock.today():
            raise ValidationFailed(
                [
                    FieldError(
                        "passport.issued_at", "PASSPORT_INVALID", "Дата выдачи паспорта в будущем"
                    )
                ]
            )
        if not await self._passports.is_valid(passport.series, passport.number):
            raise ValidationFailed(
                [FieldError("passport", "PASSPORT_INVALID", "Паспорт недействителен")]
            )

    async def _timing_dummy_hash(self) -> str:
        # Сверка с этим хешем, когда логина нет: иначе по времени ответа видно, есть ли логин.
        if self._dummy_hash is None:
            self._dummy_hash = await self._hasher.hash(secrets.token_urlsafe(32))
        return self._dummy_hash
