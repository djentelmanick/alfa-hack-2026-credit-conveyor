from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import User
from tests.fakes.auth import make_user
from tests.fakes.scenarios import KOLOS, SOKOLOV
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

PUBLIC_FIELDS = {
    "inn",
    "kind",
    "name",
    "ogrn",
    "registered_at",
    "status",
    "okved",
    "director",
    "founders",
    "address",
    "can_apply",
}


async def test_analyst_gets_company_with_bank_data(
    client: AsyncClient, analyst: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/companies/get", json={"inn": KOLOS}, headers=bearer(container, analyst)
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PUBLIC_FIELDS | {"has_account", "annual_revenue", "cannot_apply_reason"}
    assert body["inn"] == KOLOS
    assert body["kind"] == "LLC"
    assert body["registered_at"] == "2021-05-17"
    assert body["status"] == "ACTIVE"
    assert body["okved"]["code"] == "56.10"
    assert body["okved"]["label"]
    assert body["founders"] == [
        {"name": "ПЕТРОВ АЛЕКСЕЙ НИКОЛАЕВИЧ", "share_pct": 70},
        {"name": "ПЕТРОВА МАРИЯ СЕРГЕЕВНА", "share_pct": 30},
    ]
    assert body["has_account"] is True
    assert body["annual_revenue"] == "24000000.00"
    assert body["can_apply"] is False
    assert body["cannot_apply_reason"] == "Заявку подаёт клиент"


async def test_director_gets_company_and_can_apply(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/companies/get", json={"inn": KOLOS}, headers=bearer(container, director)
    )

    body = response.json()
    assert set(body) == PUBLIC_FIELDS | {"has_account", "annual_revenue"}
    assert body["can_apply"] is True


async def test_other_client_gets_only_registry_data(
    client: AsyncClient, container: Container, store: InMemoryStore
) -> None:
    accountant = await make_user(login="accountant", full_name="Бухгалтер Анна Петровна")
    await store.users.add(accountant)

    response = await client.post(
        "/api/v1/companies/get", json={"inn": KOLOS}, headers=bearer(container, accountant)
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PUBLIC_FIELDS | {"cannot_apply_reason"}
    assert body["can_apply"] is False
    assert "руководитель" in body["cannot_apply_reason"]


async def test_unknown_inn_is_not_found(
    client: AsyncClient, analyst: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/companies/get", json={"inn": "7700000000"}, headers=bearer(container, analyst)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_malformed_inn_is_validation_error(
    client: AsyncClient, analyst: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/companies/get", json={"inn": "77140123"}, headers=bearer(container, analyst)
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "inn"


async def test_director_gives_and_lists_consents(
    client: AsyncClient, director: User, container: Container
) -> None:
    headers = bearer(container, director)

    given = await client.post(
        "/api/v1/consents/give",
        json={"inn": KOLOS, "types": ["BKI_COMPANY", "BKI_PERSONAL", "PERSONAL_DATA"]},
        headers=headers,
    )
    listed = await client.post("/api/v1/consents/list", json={"inn": KOLOS}, headers=headers)

    assert given.status_code == 201
    assert given.json() == [
        {"type": t, "given_by": str(director.id), "given_at": "2026-10-24T09:00:00Z"}
        for t in ("BKI_COMPANY", "BKI_PERSONAL", "PERSONAL_DATA")
    ]
    assert listed.status_code == 200
    assert listed.json() == given.json()


async def test_not_director_cannot_give_consents(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/consents/give",
        json={"inn": SOKOLOV, "types": ["PERSONAL_DATA"]},
        headers=bearer(container, director),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_DIRECTOR"


async def test_consents_need_at_least_one_type(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/consents/give",
        json={"inn": KOLOS, "types": []},
        headers=bearer(container, director),
    )

    assert response.status_code == 422
