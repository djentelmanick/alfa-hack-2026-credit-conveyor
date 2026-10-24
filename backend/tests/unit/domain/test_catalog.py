import copy
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.domain.enums import Purpose
from app.domain.products import ProductCatalog

from .conftest import PRODUCTS_YAML

# SCOPE, раздел 2: ВКЛ только для пополнения оборотных средств.
EXPECTED = {
    Purpose.ONE_TIME_PURCHASE: ("LUMP_SUM", {"LUMP_SUM", "REVOLVING_LINE"}),
    Purpose.DEVELOPMENT: ("LUMP_SUM", {"LUMP_SUM"}),
    Purpose.REFINANCING: ("LUMP_SUM", {"LUMP_SUM"}),
    Purpose.REGULAR_PURCHASES: ("REVOLVING_LINE", {"LUMP_SUM", "REVOLVING_LINE"}),
    Purpose.SEASONAL_GAP: ("REVOLVING_LINE", {"LUMP_SUM", "REVOLVING_LINE"}),
}


def test_every_purpose_is_covered() -> None:
    assert set(EXPECTED) == set(Purpose)


@pytest.mark.parametrize("purpose", list(Purpose))
def test_recommend(catalog: ProductCatalog, purpose: Purpose) -> None:
    recommended, allowed = EXPECTED[purpose]

    recommendation = catalog.recommend(purpose)

    assert recommendation.purpose is purpose
    assert recommendation.recommended == recommended
    assert set(recommendation.allowed) == allowed
    assert recommendation.explanation


@pytest.mark.parametrize("purpose", list(Purpose))
def test_for_purpose_returns_allowed_products(catalog: ProductCatalog, purpose: Purpose) -> None:
    _, allowed = EXPECTED[purpose]

    assert {p.code for p in catalog.for_purpose(purpose)} == allowed
    for code in ("LUMP_SUM", "REVOLVING_LINE"):
        assert catalog.is_allowed(purpose, code) is (code in allowed)


@pytest.fixture
def raw_config() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(PRODUCTS_YAML.read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def test_recommended_product_must_be_allowed_for_purpose(raw_config: dict[str, Any]) -> None:
    raw_config["purposes"]["DEVELOPMENT"]["recommended"] = "REVOLVING_LINE"

    with pytest.raises(ValidationError):
        ProductCatalog.from_config(raw_config)


def test_purpose_cannot_reference_unknown_product(raw_config: dict[str, Any]) -> None:
    raw_config["purposes"]["DEVELOPMENT"]["products"].append("OVERDRAFT")

    with pytest.raises(ValidationError):
        ProductCatalog.from_config(raw_config)


def test_every_purpose_must_be_configured(raw_config: dict[str, Any]) -> None:
    del raw_config["purposes"]["SEASONAL_GAP"]

    with pytest.raises(ValidationError):
        ProductCatalog.from_config(raw_config)


def test_variant_bounds_must_be_ordered(raw_config: dict[str, Any]) -> None:
    raw_config["products"]["LUMP_SUM"]["variants"]["UNSECURED"]["amount_min"] = 40_000_000

    with pytest.raises(ValidationError):
        ProductCatalog.from_config(raw_config)


def test_unknown_product_class_is_rejected(raw_config: dict[str, Any]) -> None:
    raw_config["products"]["LUMP_SUM"]["class"] = "Overdraft"

    with pytest.raises(ValidationError):
        ProductCatalog.from_config(raw_config)
