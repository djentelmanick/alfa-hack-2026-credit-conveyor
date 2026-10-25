from typing import Self

from pydantic import BaseModel

from app.api.v1.schemas.common import MoneyStr, Percent, RateStr, RequestModel
from app.domain.enums import CollateralType, ProductCode, Purpose, Variant
from app.domain.products import LoanProduct, ProductCatalog
from app.domain.products.catalog import Recommendation


class ProductVariantOut(BaseModel):
    variant: Variant
    amount_min: MoneyStr
    amount_max: MoneyStr
    term_min: int
    term_max: int
    rate_min: RateStr
    rate_max: RateStr
    revenue_limit_pct: Percent
    collateral_coverage_min_pct: Percent | None = None
    tranche_min: MoneyStr | None = None
    tranche_term_months: int | None = None


class ProductOut(BaseModel):
    code: ProductCode
    name: str
    purposes: list[Purpose]
    variants: list[ProductVariantOut]

    @classmethod
    def from_domain(cls, product: LoanProduct, catalog: ProductCatalog) -> Self:
        cfg = product.config
        return cls(
            code=ProductCode(product.code),
            name=product.name,
            purposes=catalog.purposes_of(product.code),
            variants=[
                ProductVariantOut(
                    variant=variant,
                    amount_min=terms.amount_min,
                    amount_max=terms.amount_max,
                    term_min=terms.term_min,
                    term_max=terms.term_max,
                    rate_min=terms.rate_min,
                    rate_max=terms.rate_max,
                    revenue_limit_pct=terms.revenue_limit_pct,
                    collateral_coverage_min_pct=terms.collateral_coverage_min_pct,
                    tranche_min=cfg.tranche_min,
                    tranche_term_months=cfg.tranche_term_months,
                )
                for variant, terms in cfg.variants.items()
            ],
        )


class RecommendationIn(RequestModel):
    purpose: Purpose


class RecommendationOut(BaseModel):
    recommended: ProductCode
    allowed: list[ProductCode]
    explanation: str

    @classmethod
    def from_domain(cls, rec: Recommendation) -> Self:
        return cls(
            recommended=ProductCode(rec.recommended),
            allowed=[ProductCode(code) for code in rec.allowed],
            explanation=rec.explanation,
        )


class PurposeOut(BaseModel):
    code: Purpose
    label: str
    products: list[ProductCode]


class CollateralTypeOut(BaseModel):
    code: CollateralType
    label: str
