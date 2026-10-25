"""Ставка по классу (SCOPE раздел 6) и проверка ставки аналитика."""

from decimal import Decimal

import pytest

from app.domain.decision import RatePolicy
from app.domain.enums import RiskClass, Variant
from app.domain.errors import ValidationFailed
from app.domain.products import ProductCatalog


@pytest.mark.parametrize(
    ("product", "variant", "risk_class", "rate"),
    [
        ("LUMP_SUM", Variant.UNSECURED, RiskClass.A, "23.5"),
        ("LUMP_SUM", Variant.UNSECURED, RiskClass.B, "26.5"),
        ("LUMP_SUM", Variant.UNSECURED, RiskClass.C, "29.5"),
        ("LUMP_SUM", Variant.SECURED, RiskClass.B, "24.5"),
        ("REVOLVING_LINE", Variant.SECURED, RiskClass.A, "23"),
        ("REVOLVING_LINE", Variant.SECURED, RiskClass.B, "26"),
        ("REVOLVING_LINE", Variant.SECURED, RiskClass.C, "29"),
    ],
)
def test_rate_for_class(
    catalog: ProductCatalog, product: str, variant: Variant, risk_class: RiskClass, rate: str
) -> None:
    terms = catalog.get(product).variant(variant)
    assert RatePolicy().rate_for(terms, risk_class) == Decimal(rate)


def test_class_d_has_no_rate(catalog: ProductCatalog) -> None:
    terms = catalog.get("LUMP_SUM").variant(Variant.UNSECURED)
    with pytest.raises(ValueError, match="D"):
        RatePolicy().rate_for(terms, RiskClass.D)


@pytest.mark.parametrize(
    ("rate", "valid"),
    [("23", True), ("29", True), ("26.25", True), ("22.99", False), ("29.01", False)],
)
def test_manual_rate_must_stay_in_variant_range(
    catalog: ProductCatalog, rate: str, valid: bool
) -> None:
    terms = catalog.get("REVOLVING_LINE").variant(Variant.SECURED)
    if valid:
        RatePolicy().validate_manual(terms, Decimal(rate))
    else:
        with pytest.raises(ValidationFailed) as exc:
            RatePolicy().validate_manual(terms, Decimal(rate))
        assert exc.value.details[0].field == "rate"
