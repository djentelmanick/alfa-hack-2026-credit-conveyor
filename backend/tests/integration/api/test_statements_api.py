"""Выписки через HTTP с хранилищем в памяти (API, раздел 2.4)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import User, UserCompany
from app.domain.enums import CompanyKind
from app.infra.mocks import SyntheticData
from tests.fakes.auth import STATEMENTS_BUCKET, FixedClock, make_container, make_user
from tests.fakes.scenarios import IVANOVA
from tests.fakes.storage import InMemoryFileStorage
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

API = "/api/v1/applications"
MAX_BYTES = 10 * 1024 * 1024
STATEMENTS_DIR = (
    Path(__file__).resolve().parents[3] / "app" / "infra" / "mocks" / "data" / "statements"
)
SIGNED_12M = (STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes()


@pytest.fixture
def storage() -> InMemoryFileStorage:
    return InMemoryFileStorage()


@pytest.fixture(autouse=True)
def container(
    app: FastAPI,
    store: InMemoryStore,
    clock: FixedClock,
    synthetic: SyntheticData,
    storage: InMemoryFileStorage,
) -> Container:
    container = make_container(store.uow, clock, fixtures=synthetic, storage=storage)
    app.state.container = container
    return container


@pytest.fixture
async def ivanova(store: InMemoryStore) -> User:
    user = await make_user(
        login="ivanova",
        full_name="Иванова Елена Сергеевна",
        companies=(
            UserCompany(
                inn=IVANOVA,
                name="ИП ИВАНОВА ЕЛЕНА СЕРГЕЕВНА",
                kind=CompanyKind.IE,
                is_director=True,
            ),
        ),
    )
    await store.users.add(user)
    return user


@pytest.fixture
async def application_id(client: AsyncClient, container: Container, ivanova: User) -> str:
    headers = bearer(container, ivanova)
    consents = await client.post(
        "/api/v1/consents/give",
        json={"inn": IVANOVA, "types": ["BKI_PERSONAL", "PERSONAL_DATA"]},
        headers=headers,
    )
    assert consents.status_code == 201, consents.text
    created = await client.post(
        f"{API}/create",
        json={
            "inn": IVANOVA,
            "purpose": "DEVELOPMENT",
            "product_code": "LUMP_SUM",
            "variant": "UNSECURED",
            "amount": "3000000.00",
            "term_months": 24,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    value: str = created.json()["id"]
    return value


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    application_id: str,
    data: bytes = SIGNED_12M,
    name: str = "ivanova_12m_signed.txt",
) -> Any:
    return await client.post(
        f"{API}/statements/upload",
        data={"application_id": application_id},
        files={"file": (name, data, "text/plain")},
        headers=headers,
    )


async def test_upload_returns_statement(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    application_id: str,
    storage: InMemoryFileStorage,
) -> None:
    response = await _upload(client, bearer(container, ivanova), application_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file_name"] == "ivanova_12m_signed.txt"
    assert body["account_inn"] == IVANOVA
    assert body["inn_matches"] is True
    assert body["months_covered"] == 12
    assert body["signature_status"] == "VALID"
    assert body["annual_revenue"] == "15000000.00"
    assert storage.keys(STATEMENTS_BUCKET) == [f"statements/{application_id}/{body['id']}.txt"]

    card = await client.post(
        f"{API}/get", json={"application_id": application_id}, headers=bearer(container, ivanova)
    )
    assert [s["id"] for s in card.json()["statements"]] == [body["id"]]


async def test_file_over_10mb_is_413(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    application_id: str,
    storage: InMemoryFileStorage,
) -> None:
    headers = bearer(container, ivanova)

    too_big = await _upload(client, headers, application_id, b"x" * (MAX_BYTES + 1))
    at_limit = await _upload(client, headers, application_id, b"x" * MAX_BYTES)

    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert at_limit.status_code == 422
    assert storage.objects == {}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"%PDF-1.7", "STATEMENT_FORMAT"),
        (
            SIGNED_12M.replace(IVANOVA.encode(), b"770000000000"),
            "STATEMENT_INN_MATCH",
        ),
    ],
    ids=["not-1c", "other-inn"],
)
async def test_invalid_statement_is_422(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    application_id: str,
    data: bytes,
    code: str,
) -> None:
    response = await _upload(client, bearer(container, ivanova), application_id, data)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [(d["field"], d["code"]) for d in error["details"]] == [("file", code)]


async def test_upload_and_delete_after_submit_are_409(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    application_id: str,
) -> None:
    headers = bearer(container, ivanova)
    statement_id = (await _upload(client, headers, application_id)).json()["id"]
    submitted = await client.post(
        f"{API}/submit", json={"application_id": application_id}, headers=headers
    )
    assert submitted.status_code == 202, submitted.text

    upload = await _upload(client, headers, application_id)
    delete = await client.post(
        f"{API}/statements/delete",
        json={"application_id": application_id, "statement_id": statement_id},
        headers=headers,
    )

    assert (upload.status_code, delete.status_code) == (409, 409)
    assert delete.json()["error"]["code"] == "INVALID_STATUS"
    card = await client.post(f"{API}/get", json={"application_id": application_id}, headers=headers)
    assert [s["id"] for s in card.json()["statements"]] == [statement_id]


async def test_other_client_gets_403(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    director: User,
    application_id: str,
) -> None:
    statement_id = (await _upload(client, bearer(container, ivanova), application_id)).json()["id"]
    body = {"application_id": application_id, "statement_id": statement_id}
    stranger = bearer(container, director)

    upload = await _upload(client, stranger, application_id)
    delete = await client.post(f"{API}/statements/delete", json=body, headers=stranger)
    url = await client.post(f"{API}/statements/download-url", json=body, headers=stranger)

    assert [r.status_code for r in (upload, delete, url)] == [403, 403, 403]
    assert url.json()["error"]["code"] == "FORBIDDEN"


async def test_owner_and_analyst_get_download_url(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    analyst: User,
    application_id: str,
) -> None:
    statement_id = (await _upload(client, bearer(container, ivanova), application_id)).json()["id"]
    body = {"application_id": application_id, "statement_id": statement_id}

    for user in (ivanova, analyst):
        response = await client.post(
            f"{API}/statements/download-url", json=body, headers=bearer(container, user)
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "url": (
                f"http://files.test/{STATEMENTS_BUCKET}/statements/{application_id}/"
                f"{statement_id}.txt?ttl=300&name=ivanova_12m_signed.txt"
            ),
            "file_name": "ivanova_12m_signed.txt",
            "expires_at": "2026-10-24T09:05:00Z",
        }


async def test_delete_removes_statement_and_file(
    client: AsyncClient,
    container: Container,
    ivanova: User,
    application_id: str,
    storage: InMemoryFileStorage,
) -> None:
    headers = bearer(container, ivanova)
    statement_id = (await _upload(client, headers, application_id)).json()["id"]

    response = await client.post(
        f"{API}/statements/delete",
        json={"application_id": application_id, "statement_id": statement_id},
        headers=headers,
    )

    assert response.status_code == 204
    assert response.content == b""
    assert storage.objects == {}
    card = await client.post(f"{API}/get", json={"application_id": application_id}, headers=headers)
    assert card.json()["statements"] == []
