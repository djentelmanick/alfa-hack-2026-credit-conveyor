"""Подача заявки представителем: проверка доверенности, привязка компании и повторная проверка."""

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.authority import PoaRequest
from app.domain.entities import User
from app.domain.enums import PoaKind, SourceType
from app.infra.mocks import MockFailures, SyntheticData
from tests.fakes.auth import FixedClock, make_container, make_user
from tests.fakes.scenarios import KHLEBNY_DVOR, TECHPROM
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

GUID_BUH = "5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e01"
GUID_LEBEDEV = "5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e02"
GUID_REVOKED = "5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e03"
LLC_CONSENTS = ["BKI_COMPANY", "BKI_PERSONAL", "PERSONAL_DATA"]


def _claim(inn: str, role: str, guid: str | None) -> dict[str, Any]:
    poa = {"kind": "MCHD", "mchd_guid": guid} if guid else None
    return {"inn": inn, "role": role, "power_of_attorney": poa}


def _form() -> dict[str, Any]:
    return {
        "inn": KHLEBNY_DVOR,
        "purpose": "DEVELOPMENT",
        "product_code": "LUMP_SUM",
        "variant": "UNSECURED",
        "amount": "2000000.00",
        "term_months": 12,
        "owner_guarantee_consent": True,
    }


def _codes(body: dict[str, Any]) -> list[str]:
    return [d["code"] for d in body["error"]["details"]]


@pytest.fixture
async def buh(store: InMemoryStore) -> User:
    user = await make_user(login="buh", full_name="Васильева Анна Игоревна")
    await store.users.add(user)
    return user


@pytest.fixture
async def lebedev(store: InMemoryStore) -> User:
    user = await make_user(login="lebedev", full_name="Лебедев Павел Олегович")
    await store.users.add(user)
    return user


async def _post(
    client: AsyncClient, container: Container, user: User, path: str, body: dict[str, Any]
) -> Any:
    return await client.post(f"/api/v1{path}", json=body, headers=bearer(container, user))


async def test_accountant_applies_after_mchd_is_verified(
    client: AsyncClient, container: Container, buh: User
) -> None:
    before = (await _post(client, container, buh, "/companies/get", {"inn": KHLEBNY_DVOR})).json()
    assert (before["can_apply"], before["authority_required"]) == (False, True)
    assert "has_account" not in before
    no_link = await _post(
        client, container, buh, "/consents/give", {"inn": KHLEBNY_DVOR, "types": LLC_CONSENTS}
    )
    assert no_link.json()["error"]["code"] == "NOT_DIRECTOR"

    claimed = await _post(
        client,
        container,
        buh,
        "/companies/authority",
        _claim(KHLEBNY_DVOR, "FINANCE_OFFICER", GUID_BUH),
    )

    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    assert (body["can_apply"], body["authority_required"], body["has_account"]) == (
        True,
        False,
        True,
    )
    assert body["authority"] == {
        "role": "FINANCE_OFFICER",
        "basis": "POWER_OF_ATTORNEY",
        "representative_name": "Васильева Анна Игоревна",
        "power_of_attorney": f"МЧД {GUID_BUH}",
    }
    me = (await client.get("/api/v1/auth/me", headers=bearer(container, buh))).json()
    assert me["companies"][0]["basis"] == "POWER_OF_ATTORNEY"
    assert me["companies"][0]["is_director"] is False

    consents = await _post(
        client, container, buh, "/consents/give", {"inn": KHLEBNY_DVOR, "types": LLC_CONSENTS}
    )
    assert consents.status_code == 201, consents.text
    created = await _post(client, container, buh, "/applications/create", _form())
    assert created.status_code == 201, created.text
    assert created.json()["authority"]["basis"] == "POWER_OF_ATTORNEY"
    submitted = await _post(
        client, container, buh, "/applications/submit", {"application_id": created.json()["id"]}
    )
    assert submitted.json()["status"] == "SUBMITTED", submitted.text


async def test_mchd_without_signing_power_is_refused(
    client: AsyncClient, container: Container, lebedev: User
) -> None:
    response = await _post(
        client, container, lebedev, "/companies/authority", _claim(TECHPROM, "OWNER", GUID_LEBEDEV)
    )

    assert response.status_code == 422
    assert _codes(response.json()) == ["POA_MISSING_POWER"]
    after = (await _post(client, container, lebedev, "/companies/get", {"inn": TECHPROM})).json()
    assert after["can_apply"] is False


async def test_claim_without_poa_is_forbidden(
    client: AsyncClient, container: Container, buh: User
) -> None:
    response = await _post(
        client,
        container,
        buh,
        "/companies/authority",
        _claim(KHLEBNY_DVOR, "FINANCE_OFFICER", None),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_DIRECTOR"


@pytest.mark.parametrize(
    "poa",
    [
        {"kind": "MCHD"},
        {"kind": "NOTARIAL", "notarized_on": "2026-09-15", "register_number": "77/1"},
        {"kind": "SIMPLE"},
    ],
)
async def test_incomplete_or_simple_poa_is_422(
    client: AsyncClient, container: Container, buh: User, poa: dict[str, Any]
) -> None:
    body = {"inn": KHLEBNY_DVOR, "role": "FINANCE_OFFICER", "power_of_attorney": poa}
    response = await _post(client, container, buh, "/companies/authority", body)
    assert response.status_code == 422


async def test_broken_mchd_registry_is_503(
    app: FastAPI, client: AsyncClient, store: InMemoryStore, synthetic: SyntheticData, buh: User
) -> None:
    failures = MockFailures()
    await failures.set(SourceType.MCHD, KHLEBNY_DVOR, enabled=True)
    container = make_container(store.uow, FixedClock(), fixtures=synthetic, failures=failures)
    app.state.container = container

    response = await _post(
        client,
        container,
        buh,
        "/companies/authority",
        _claim(KHLEBNY_DVOR, "FINANCE_OFFICER", GUID_BUH),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOURCE_UNAVAILABLE"


async def test_poa_revoked_after_linking_blocks_submit(
    client: AsyncClient, container: Container, store: InMemoryStore, buh: User
) -> None:
    await _post(
        client,
        container,
        buh,
        "/companies/authority",
        _claim(KHLEBNY_DVOR, "FINANCE_OFFICER", GUID_BUH),
    )
    await _post(
        client, container, buh, "/consents/give", {"inn": KHLEBNY_DVOR, "types": LLC_CONSENTS}
    )
    application_id = (await _post(client, container, buh, "/applications/create", _form())).json()[
        "id"
    ]
    # Между привязкой и подачей доверенность отозвали: в реестре теперь только отозванная.
    user = await store.users.get(buh.id)
    [link] = user.companies
    assert link.authority is not None
    revoked = PoaRequest(kind=PoaKind.MCHD, mchd_guid=UUID(GUID_REVOKED))
    user.companies = [replace(link, authority=replace(link.authority, poa=revoked))]

    response = await _post(
        client, container, buh, "/applications/submit", {"application_id": application_id}
    )

    assert response.status_code == 422
    assert "POA_REVOKED" in _codes(response.json())
    card = await _post(
        client, container, buh, "/applications/get", {"application_id": application_id}
    )
    assert card.json()["status"] == "DRAFT"


async def test_director_still_applies_without_poa(
    client: AsyncClient, container: Container, director: User
) -> None:
    body = (
        await _post(client, container, director, "/companies/get", {"inn": "7714012340"})
    ).json()
    assert body["can_apply"] is True
    assert body["authority"]["basis"] == "DIRECTOR"
