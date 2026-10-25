import base64
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest

from app.domain.enums import Role
from app.domain.errors import TokenExpired, TokenInvalid
from app.infra.security.jwt import REQUIRED_CLAIMS, JwtService
from tests.fakes.auth import JWT_SECRET, TOKEN_TTL, FixedClock, jwt_service, make_user
from tests.fakes.scenarios import NOW


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def service(clock: FixedClock) -> JwtService:
    return jwt_service(clock)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sub": str(uuid4()),
        "role": "CLIENT",
        "name": "Петров Алексей Николаевич",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + TOKEN_TTL).timestamp()),
        "jti": uuid4().hex,
    }
    payload.update(overrides)
    return payload


def _encode(payload: dict[str, Any], secret: str = JWT_SECRET, alg: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=alg)


async def test_issued_token_decodes_to_user_claims(service: JwtService) -> None:
    user = await make_user(role=Role.ANALYST)

    claims = service.decode(service.issue(user))

    assert claims.user_id == user.id
    assert claims.role is Role.ANALYST
    assert claims.name == user.full_name
    assert claims.issued_at == NOW
    assert claims.expires_at == NOW + TOKEN_TTL
    assert claims.token_id


async def test_each_token_gets_own_jti(service: JwtService) -> None:
    user = await make_user()

    first, second = service.decode(service.issue(user)), service.decode(service.issue(user))

    assert first.token_id != second.token_id


async def test_token_is_valid_until_last_second(service: JwtService, clock: FixedClock) -> None:
    token = service.issue(await make_user())
    clock.advance(TOKEN_TTL - timedelta(seconds=1))

    assert service.decode(token).expires_at == NOW + TOKEN_TTL


async def test_token_expires_exactly_at_exp(service: JwtService, clock: FixedClock) -> None:
    token = service.issue(await make_user())
    clock.advance(TOKEN_TTL)

    with pytest.raises(TokenExpired):
        service.decode(token)


def test_signature_with_other_secret_is_invalid(service: JwtService) -> None:
    token = _encode(_payload(), secret="other-secret-" + "y" * 32)

    with pytest.raises(TokenInvalid):
        service.decode(token)


def test_tampered_payload_is_invalid(service: JwtService) -> None:
    header, _, signature = _encode(_payload()).split(".")
    forged = base64.urlsafe_b64encode(json.dumps(_payload(role="ANALYST")).encode()).rstrip(b"=")

    with pytest.raises(TokenInvalid):
        service.decode(f"{header}.{forged.decode()}.{signature}")


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer abc"])
def test_malformed_token_is_invalid(service: JwtService, token: str) -> None:
    with pytest.raises(TokenInvalid):
        service.decode(token)


def test_unsigned_token_is_invalid(service: JwtService) -> None:
    token = jwt.encode(_payload(), key=None, algorithm="none")

    with pytest.raises(TokenInvalid):
        service.decode(token)


def test_other_hmac_algorithm_is_invalid(service: JwtService) -> None:
    with pytest.raises(TokenInvalid):
        service.decode(_encode(_payload(), alg="HS512"))


@pytest.mark.parametrize("claim", REQUIRED_CLAIMS)
def test_missing_required_claim_is_invalid(service: JwtService, claim: str) -> None:
    payload = _payload()
    del payload[claim]

    with pytest.raises(TokenInvalid):
        service.decode(_encode(payload))


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "ADMIN"},
        {"sub": "not-a-uuid"},
        {"sub": 42},
        {"name": 42},
        {"jti": 42},
        {"iat": "yesterday"},
        {"exp": True},
    ],
    ids=["role", "sub-format", "sub-type", "name", "jti", "iat", "exp"],
)
def test_claim_of_wrong_type_is_invalid(service: JwtService, overrides: dict[str, Any]) -> None:
    with pytest.raises(TokenInvalid):
        service.decode(_encode(_payload(**overrides)))


def test_expired_token_with_valid_signature_is_expired_not_invalid(service: JwtService) -> None:
    token = _encode(_payload(iat=int((NOW - 2 * TOKEN_TTL).timestamp()), exp=int(NOW.timestamp())))

    with pytest.raises(TokenExpired):
        service.decode(token)
