from datetime import timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import User
from app.domain.enums import Role
from tests.fakes.auth import PASSWORD, TOKEN_TTL, FixedClock, make_user
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

REGISTER_BODY: dict[str, Any] = {
    "login": "new-client",
    "password": PASSWORD,
    "full_name": "Петров Алексей Николаевич",
    "inn": "771401234587",
    "passport": {"series": "4510", "number": "654321", "issued_at": "2015-03-01"},
}

# Все защищённые эндпоинты этапа 4: (метод, путь, тело).
PROTECTED = [
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/products", None),
    ("POST", "/api/v1/products/recommendation", {"purpose": "DEVELOPMENT"}),
    ("GET", "/api/v1/dictionaries/purposes", None),
    ("GET", "/api/v1/dictionaries/collateral-types", None),
    ("POST", "/api/v1/companies/get", {"inn": "7714012340"}),
    ("POST", "/api/v1/consents/give", {"inn": "7714012340", "types": ["PERSONAL_DATA"]}),
    ("POST", "/api/v1/consents/list", {"inn": "7714012340"}),
]


def _error_code(body: dict[str, Any]) -> str:
    code: str = body["error"]["code"]
    return code


# --- Регистрация и вход ---


async def test_register_returns_user_out(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=REGISTER_BODY)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "role", "full_name", "inn", "passport_verified", "companies"}
    assert body["role"] == "CLIENT"
    assert body["full_name"] == "Петров Алексей Николаевич"
    assert body["passport_verified"] is True
    assert body["companies"] == []


async def test_register_with_taken_login_is_conflict(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=REGISTER_BODY)

    response = await client.post("/api/v1/auth/register", json=REGISTER_BODY)

    assert response.status_code == 409
    assert _error_code(response.json()) == "LOGIN_TAKEN"


async def test_register_with_bad_inn_checksum_is_validation_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={**REGISTER_BODY, "inn": "771401234588"}
    )

    assert response.status_code == 422
    [detail] = response.json()["error"]["details"]
    assert (detail["field"], detail["code"]) == ("inn", "INN_INVALID")


async def test_register_with_taken_inn_is_conflict(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=REGISTER_BODY)

    response = await client.post(
        "/api/v1/auth/register", json={**REGISTER_BODY, "login": "another-login"}
    )

    assert response.status_code == 409
    assert _error_code(response.json()) == "INN_TAKEN"


async def test_register_without_inn_is_rejected(client: AsyncClient) -> None:
    body = {k: v for k, v in REGISTER_BODY.items() if k != "inn"}
    response = await client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 422


async def test_register_with_invalid_passport_is_validation_error(client: AsyncClient) -> None:
    body = {**REGISTER_BODY, "passport": {**REGISTER_BODY["passport"], "series": "4500"}}
    body["passport"]["number"] = "000000"

    response = await client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "passport"
    assert error["details"][0]["code"] == "PASSPORT_INVALID"


async def test_register_does_not_accept_role_from_body(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json={**REGISTER_BODY, "role": "ANALYST"})

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "role"


async def test_login_returns_login_out(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"login": "kolos", "password": PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == int(TOKEN_TTL.total_seconds())
    assert body["user"]["id"] == str(director.id)
    assert body["user"]["companies"] == [
        {
            "inn": "7714012340",
            "name": 'ООО "ПЕКАРНЯ КОЛОС"',
            "kind": "LLC",
            "is_director": True,
            "basis": None,
        }
    ]
    assert container.tokens.decode(body["access_token"]).user_id == director.id


@pytest.mark.parametrize(
    "credentials",
    [{"login": "kolos", "password": "wrong-password"}, {"login": "nobody", "password": PASSWORD}],
    ids=["wrong-password", "unknown-login"],
)
async def test_login_with_bad_credentials_is_unauthorized(
    client: AsyncClient, director: User, credentials: dict[str, str]
) -> None:
    response = await client.post("/api/v1/auth/login", json=credentials)

    assert response.status_code == 401
    assert _error_code(response.json()) == "INVALID_CREDENTIALS"


async def test_me_returns_user_from_token(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.get("/api/v1/auth/me", headers=bearer(container, director))

    assert response.status_code == 200
    assert response.json()["id"] == str(director.id)


async def test_me_for_deleted_user_is_token_invalid(
    client: AsyncClient, director: User, container: Container, store: InMemoryStore
) -> None:
    headers = bearer(container, director)
    del store.users.items[director.id]

    response = await client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_INVALID"


# --- Ошибки токена ---


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
async def test_protected_endpoint_without_token_is_token_missing(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = await client.request(method, path, json=body)

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_MISSING"


@pytest.mark.parametrize(
    "header",
    [
        "Basic a29sb3M6cGFzc3dvcmQ=",
        "Bearer",
        "Bearer ",
        "Bearer not-a-jwt",
        "Token eyJhbGciOiJIUzI1NiJ9.e30.x",
    ],
    ids=["basic-scheme", "no-token", "blank-token", "garbage", "other-scheme"],
)
async def test_malformed_authorization_is_token_invalid(client: AsyncClient, header: str) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_INVALID"


async def test_empty_authorization_header_is_token_missing(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": ""})

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_MISSING"


async def test_token_signed_with_other_secret_is_token_invalid(
    client: AsyncClient, director: User, container: Container
) -> None:
    claims = jwt.decode(
        container.tokens.issue(director), options={"verify_signature": False, "verify_exp": False}
    )
    forged = jwt.encode(claims, "attacker-secret-" + "z" * 48, algorithm="HS256")

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_INVALID"


async def test_expired_token_is_token_expired(
    client: AsyncClient, director: User, container: Container, clock: FixedClock
) -> None:
    headers = bearer(container, director)
    clock.advance(TOKEN_TTL + timedelta(seconds=1))

    response = await client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_EXPIRED"


async def test_scheme_name_is_case_insensitive(
    client: AsyncClient, director: User, container: Container
) -> None:
    token = container.tokens.issue(director)

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"bearer {token}"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/auth/register", REGISTER_BODY),
        ("/api/v1/auth/login", {"login": "nobody", "password": PASSWORD}),
    ],
)
async def test_public_auth_endpoints_do_not_require_token(
    client: AsyncClient, path: str, body: dict[str, Any]
) -> None:
    response = await client.post(path, json=body)

    assert response.json().get("error", {}).get("code") not in {"TOKEN_MISSING", "TOKEN_INVALID"}


# --- Роли ---


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/products/recommendation", {"purpose": "DEVELOPMENT"}),
        ("/api/v1/consents/give", {"inn": "7714012340", "types": ["PERSONAL_DATA"]}),
        ("/api/v1/consents/list", {"inn": "7714012340"}),
    ],
)
async def test_client_only_endpoints_forbid_analyst(
    client: AsyncClient, analyst: User, container: Container, path: str, body: dict[str, Any]
) -> None:
    response = await client.post(path, json=body, headers=bearer(container, analyst))

    assert response.status_code == 403
    assert _error_code(response.json()) == "FORBIDDEN"


@pytest.mark.parametrize("role", [Role.CLIENT, Role.ANALYST])
async def test_shared_endpoints_allow_both_roles(
    client: AsyncClient, container: Container, store: InMemoryStore, role: Role
) -> None:
    user = await make_user(role=role, full_name="Петров Алексей Николаевич")
    await store.users.add(user)
    headers = bearer(container, user)

    for method, path, body in PROTECTED:
        if path in {
            "/api/v1/products/recommendation",
            "/api/v1/consents/give",
            "/api/v1/consents/list",
        }:
            continue
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 200, (path, response.json())
