from pathlib import Path

import pytest
import yaml

from app.domain.products import ProductCatalog

PRODUCTS_YAML = Path(__file__).resolve().parents[3] / "app" / "infra" / "config" / "products.yaml"


@pytest.fixture(scope="session")
def catalog() -> ProductCatalog:
    # Тесты домена идут по настоящему конфигу, чтобы числа SCOPE проверялись там, где они живут.
    raw = yaml.safe_load(PRODUCTS_YAML.read_text(encoding="utf-8"))
    return ProductCatalog.from_config(raw)
