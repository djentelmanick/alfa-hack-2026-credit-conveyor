from datetime import date
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import LoanContract, User
from app.domain.enums import Variant
from app.domain.value_objects import Money
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer


async def test_emulation_is_hidden_without_demo_mode(
    client: AsyncClient, container: Container, store: InMemoryStore, analyst: User
) -> None:
    # Договор существует и аналитику виден: 404 возможен только из-за выключенного DEMO_MODE.
    contract = LoanContract(
        id=uuid4(),
        application_id=uuid4(),
        number="КД-2026-000001",
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        rate=Decimal("23.5"),
        term_months=12,
        start_date=date(2026, 10, 24),
        contract_date=date(2026, 10, 24),
        principal=Money.of(2_000_000),
        outstanding=Money.of(2_000_000),
    )
    await store.contracts.add(contract)
    headers = bearer(container, analyst)
    visible = await client.post(
        "/api/v1/contracts/versions", json={"contract_id": str(contract.id)}, headers=headers
    )
    assert visible.status_code == 200

    response = await client.post(
        "/api/v1/contracts/emulation/next-month",
        json={"contract_id": str(contract.id)},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
