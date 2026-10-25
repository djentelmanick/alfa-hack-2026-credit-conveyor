from pathlib import Path

import yaml

from app.domain.products import ProductCatalog

PRODUCTS_YAML = Path(__file__).with_name("products.yaml")


def load_catalog(path: Path = PRODUCTS_YAML) -> ProductCatalog:
    return ProductCatalog.from_config(yaml.safe_load(path.read_text(encoding="utf-8")))
