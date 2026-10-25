from typing import Any

import pytest
from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import User
from tests.integration.api.conftest import bearer


async def _get(client: AsyncClient, path: str, headers: dict[str, str]) -> Any:
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, response.json()
    return response.json()


async def test_products_match_products_yaml(
    client: AsyncClient, director: User, container: Container
) -> None:
    products = await _get(client, "/api/v1/products", bearer(container, director))

    by_code = {p["code"]: p for p in products}
    assert set(by_code) == {"LUMP_SUM", "REVOLVING_LINE"}

    lump = by_code["LUMP_SUM"]
    assert lump["name"] == "Кредит одной суммой"
    assert set(lump["purposes"]) == {
        "ONE_TIME_PURCHASE",
        "DEVELOPMENT",
        "REFINANCING",
        "REGULAR_PURCHASES",
        "SEASONAL_GAP",
    }
    unsecured, secured = lump["variants"]
    # Деньги и ставки строками, проценты числами, необязательные поля не приходят (API.md, 4.3).
    assert unsecured == {
        "variant": "UNSECURED",
        "amount_min": "300000.00",
        "amount_max": "30000000.00",
        "term_min": 12,
        "term_max": 60,
        "rate_min": "23.5",
        "rate_max": "29.5",
        "revenue_limit_pct": 30,
    }
    assert secured["collateral_coverage_min_pct"] == 50
    assert secured["amount_max"] == "150000000.00"

    line = by_code["REVOLVING_LINE"]
    assert set(line["purposes"]) == {"ONE_TIME_PURCHASE", "REGULAR_PURCHASES", "SEASONAL_GAP"}
    line_secured = line["variants"][1]
    assert line_secured["rate_min"] == "23.0"
    assert line_secured["tranche_min"] == "50000.00"
    assert line_secured["tranche_term_months"] == 6


@pytest.mark.parametrize(
    ("purpose", "recommended", "allowed"),
    [
        ("DEVELOPMENT", "LUMP_SUM", ["LUMP_SUM"]),
        ("SEASONAL_GAP", "REVOLVING_LINE", ["LUMP_SUM", "REVOLVING_LINE"]),
    ],
)
async def test_recommendation_by_purpose(
    client: AsyncClient,
    director: User,
    container: Container,
    purpose: str,
    recommended: str,
    allowed: list[str],
) -> None:
    response = await client.post(
        "/api/v1/products/recommendation",
        json={"purpose": purpose},
        headers=bearer(container, director),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"recommended", "allowed", "explanation"}
    assert (body["recommended"], body["allowed"]) == (recommended, allowed)
    assert body["explanation"]


async def test_recommendation_for_unknown_purpose_is_validation_error(
    client: AsyncClient, director: User, container: Container
) -> None:
    response = await client.post(
        "/api/v1/products/recommendation",
        json={"purpose": "HOLIDAY"},
        headers=bearer(container, director),
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "purpose"


async def test_purposes_dictionary(
    client: AsyncClient, director: User, container: Container
) -> None:
    purposes = await _get(client, "/api/v1/dictionaries/purposes", bearer(container, director))

    assert [p["code"] for p in purposes] == [
        "ONE_TIME_PURCHASE",
        "DEVELOPMENT",
        "REFINANCING",
        "REGULAR_PURCHASES",
        "SEASONAL_GAP",
    ]
    assert purposes[1] == {"code": "DEVELOPMENT", "label": "Развитие", "products": ["LUMP_SUM"]}


async def test_collateral_types_dictionary(
    client: AsyncClient, director: User, container: Container
) -> None:
    types = await _get(client, "/api/v1/dictionaries/collateral-types", bearer(container, director))

    assert [t["code"] for t in types] == ["REAL_ESTATE", "VEHICLE", "EQUIPMENT", "GOODS"]
    assert all(set(t) == {"code", "label"} and t["label"] for t in types)
