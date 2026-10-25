from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt

from app.domain.entities import TokenClaims, User
from app.domain.enums import Role
from app.domain.errors import TokenExpired, TokenInvalid
from app.domain.ports import Clock, TokenService

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "role", "name", "iat", "exp", "jti")


class JwtService(TokenService):
    def __init__(self, secret: str, ttl: timedelta, clock: Clock) -> None:
        self._secret = secret
        self._ttl = ttl
        self._clock = clock

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def issue(self, user: User) -> str:
        now = self._clock.now()
        payload = {
            "sub": str(user.id),
            "role": user.role.value,
            "name": user.full_name,
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": uuid4().hex,
        }
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)

    def decode(self, token: str) -> TokenClaims:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                # PyJWT сверяет exp и iat с системными часами, а время в приложении идёт
                # только через Clock, поэтому эти проверки делаем сами ниже.
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise TokenInvalid() from exc

        claims = _parse_claims(payload)
        if self._clock.now() >= claims.expires_at:
            raise TokenExpired()
        return claims


def _parse_claims(payload: dict[str, Any]) -> TokenClaims:
    try:
        iat, exp = payload["iat"], payload["exp"]
        name, jti = payload["name"], payload["jti"]
        # bool это подкласс int, а true вместо времени это битый токен.
        if not all(type(v) is int for v in (iat, exp)) or not all(
            isinstance(v, str) for v in (name, jti)
        ):
            raise TokenInvalid()
        return TokenClaims(
            user_id=UUID(payload["sub"]),
            role=Role(payload["role"]),
            name=name,
            issued_at=datetime.fromtimestamp(iat, UTC),
            expires_at=datetime.fromtimestamp(exp, UTC),
            token_id=jti,
        )
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise TokenInvalid() from exc
