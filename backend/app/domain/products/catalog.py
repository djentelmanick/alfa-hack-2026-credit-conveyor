from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from app.domain.enums import CollateralType, Purpose
from app.domain.errors import NotFound
from app.domain.products.config import CatalogConfig, ProductConfig
from app.domain.products.loan_product import LoanProduct, LumpSumLoan, RevolvingCreditLine

_PRODUCT_CLASSES: Mapping[str, type[LoanProduct]] = {
    "LumpSumLoan": LumpSumLoan,
    "RevolvingCreditLine": RevolvingCreditLine,
}


@dataclass(frozen=True, kw_only=True, slots=True)
class Recommendation:
    purpose: Purpose
    label: str
    recommended: str
    allowed: tuple[str, ...]
    explanation: str


class ProductCatalog:
    """Фабрика продуктов из products.yaml.

    YAML читает infra: домену разрешены только stdlib и Pydantic, поэтому сюда приходит словарь.
    """

    def __init__(self, config: CatalogConfig) -> None:
        self.config = config
        self._products: dict[str, LoanProduct] = {
            code: self._build(code, cfg) for code, cfg in config.products.items()
        }

    @classmethod
    def from_config(cls, raw: Mapping[str, object]) -> Self:
        return cls(CatalogConfig.model_validate(raw))

    @staticmethod
    def _build(code: str, cfg: ProductConfig) -> LoanProduct:
        return _PRODUCT_CLASSES[cfg.product_class](code, cfg)

    def all(self) -> list[LoanProduct]:
        return list(self._products.values())

    def get(self, code: str) -> LoanProduct:
        try:
            return self._products[code]
        except KeyError:
            raise NotFound(f"Продукт {code} не найден") from None

    def purposes_of(self, product_code: str) -> list[Purpose]:
        return [p for p, cfg in self.config.purposes.items() if product_code in cfg.products]

    def recommendations(self) -> list[Recommendation]:
        return [self.recommend(purpose) for purpose in self.config.purposes]

    def collateral_types(self) -> dict[CollateralType, str]:
        return dict(self.config.collateral_types)

    def for_purpose(self, purpose: Purpose) -> list[LoanProduct]:
        return [self._products[code] for code in self.config.purposes[purpose].products]

    def is_allowed(self, purpose: Purpose, product_code: str) -> bool:
        return product_code in self.config.purposes[purpose].products

    def recommend(self, purpose: Purpose) -> Recommendation:
        cfg = self.config.purposes[purpose]
        return Recommendation(
            purpose=purpose,
            label=cfg.label,
            recommended=cfg.recommended,
            allowed=tuple(cfg.products),
            explanation=cfg.explanation,
        )
