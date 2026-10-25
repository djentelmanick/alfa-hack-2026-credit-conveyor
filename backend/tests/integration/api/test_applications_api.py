"""Заявки клиента через HTTP без БД: дубль по ИНН, чужая заявка, авторизация WebSocket."""

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1.deps import Container
from app.domain.entities import Application, User, UserCompany
from app.domain.enums import CompanyKind
from tests.fakes.auth import make_user
from tests.fakes.scenarios import KOLOS, ROMASHKA
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

WS_URL = "/api/v1/ws/applications"


def kolos_form(**overrides: Any) -> dict[str, Any]:
    body = {
        "inn": KOLOS,
        "purpose": "DEVELOPMENT",
        "product_code": "LUMP_SUM",
        "variant": "UNSECURED",
        "amount": "2000000.00",
        "term_months": 12,
        "owner_guarantee_consent": True,
    }
    return body | overrides


@pytest.fixture
async def stranger(store: InMemoryStore) -> User:
    user = await make_user(
        login="romashka",
        full_name="Смирнова Ольга Викторовна",
        companies=(
            UserCompany(inn=ROMASHKA, name='ООО "РОМАШКА"', kind=CompanyKind.LLC, is_director=True),
        ),
    )
    await store.users.add(user)
    return user


async def _ready_draft(client: AsyncClient, container: Container, user: User) -> str:
    headers = bearer(container, user)
    consents = await client.post(
        "/api/v1/consents/give",
        json={"inn": KOLOS, "types": ["BKI_COMPANY", "BKI_PERSONAL", "PERSONAL_DATA"]},
        headers=headers,
    )
    assert consents.status_code == 201, consents.text
    created = await client.post("/api/v1/applications/create", json=kolos_form(), headers=headers)
    assert created.status_code == 201, created.text
    application_id: str = created.json()["id"]
    return application_id


async def test_second_application_for_same_inn_is_rejected_on_create(
    client: AsyncClient, container: Container, director: User
) -> None:
    await _ready_draft(client, container, director)

    response = await client.post(
        "/api/v1/applications/create", json=kolos_form(), headers=bearer(container, director)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACTIVE_APPLICATION_EXISTS"


async def test_submit_is_rejected_when_another_application_became_active(
    client: AsyncClient, container: Container, director: User, store: InMemoryStore
) -> None:
    first = await _ready_draft(client, container, director)
    # Второй черновик кладём в обход create: он дубль уже не пустит, а submit проверяет заново.
    original = await store.applications.get(UUID(first))
    duplicate = Application(
        id=uuid4(),
        applicant_id=original.applicant_id,
        inn=original.inn,
        company_kind=original.company_kind,
        purpose=original.purpose,
        product_code=original.product_code,
        variant=original.variant,
        amount=original.amount,
        term_months=original.term_months,
        owner_guarantee=original.owner_guarantee,
        consents=original.consents,
        created_at=original.created_at,
    )
    await store.applications.add(duplicate)

    response = await client.post(
        "/api/v1/applications/submit",
        json={"application_id": str(duplicate.id)},
        headers=bearer(container, director),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACTIVE_APPLICATION_EXISTS"


@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ("/api/v1/applications/get", {}),
        ("/api/v1/applications/update", {"term_months": 24}),
        ("/api/v1/applications/validate", {}),
        ("/api/v1/applications/submit", {}),
        ("/api/v1/applications/history", {}),
        ("/api/v1/applications/decision", {}),
        ("/api/v1/applications/cancel", {}),
    ],
)
async def test_foreign_application_is_forbidden(
    client: AsyncClient,
    container: Container,
    director: User,
    stranger: User,
    path: str,
    extra: dict[str, Any],
) -> None:
    application_id = await _ready_draft(client, container, director)

    response = await client.post(
        path, json={"application_id": application_id} | extra, headers=bearer(container, stranger)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "subprotocols",
    [None, ["bearer"], ["bearer", "not-a-jwt"], ["token", "x"]],
    ids=["no-protocol", "no-token", "bad-token", "wrong-scheme"],
)
def test_websocket_without_valid_token_is_closed_with_4401(
    app: FastAPI, subprotocols: list[str] | None
) -> None:
    client = TestClient(app)

    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect(WS_URL, subprotocols=subprotocols) as ws,
    ):
        ws.receive_json()

    assert closed.value.code == 4401
