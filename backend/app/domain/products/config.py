from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import CollateralType, Purpose, Variant
from app.domain.value_objects import Money


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class VariantConfig(_Config):
    amount_min: Decimal
    amount_max: Decimal
    term_min: int = Field(gt=0)
    term_max: int = Field(gt=0)
    rate_min: Decimal
    rate_max: Decimal
    revenue_limit_pct: Decimal = Field(gt=0, le=100)
    collateral_coverage_min_pct: Decimal | None = Field(default=None, gt=0, le=100)
    auto_prolong: bool = False
    draw_stop_months_before_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> Self:
        for low, high in (
            ("amount_min", "amount_max"),
            ("term_min", "term_max"),
            ("rate_min", "rate_max"),
        ):
            if getattr(self, low) > getattr(self, high):
                raise ValueError(f"{low} больше {high}")
        return self

    @property
    def min_amount(self) -> Money:
        return Money(self.amount_min)

    @property
    def max_amount(self) -> Money:
        return Money(self.amount_max)


class ProductConfig(_Config):
    name: str
    product_class: Literal["LumpSumLoan", "RevolvingCreditLine"] = Field(alias="class")
    variants: dict[Variant, VariantConfig]
    tranche_min: Decimal | None = None
    tranche_term_months: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _line_has_tranche_settings(self) -> Self:
        if self.product_class == "RevolvingCreditLine" and (
            self.tranche_min is None or self.tranche_term_months is None
        ):
            raise ValueError("Для ВКЛ нужны tranche_min и tranche_term_months")
        return self


class PurposeConfig(_Config):
    label: str
    products: list[str] = Field(min_length=1)
    recommended: str
    explanation: str

    @model_validator(mode="after")
    def _recommended_is_allowed(self) -> Self:
        if self.recommended not in self.products:
            raise ValueError(f"Рекомендуемый продукт {self.recommended} не входит в products")
        return self


class CatalogConfig(_Config):
    purposes: dict[Purpose, PurposeConfig]
    products: dict[str, ProductConfig]
    collateral_types: dict[CollateralType, str]
    industry_risk: dict[Literal["low", "medium", "high"], list[str]]
    forbidden_okved: list[str]

    @model_validator(mode="after")
    def _references_are_consistent(self) -> Self:
        missing = set(Purpose) - set(self.purposes)
        if missing:
            raise ValueError(f"Нет настроек для целей: {sorted(missing)}")
        missing_collateral = set(CollateralType) - set(self.collateral_types)
        if missing_collateral:
            raise ValueError(f"Нет подписей для типов залога: {sorted(missing_collateral)}")
        for purpose, cfg in self.purposes.items():
            unknown = set(cfg.products) - set(self.products)
            if unknown:
                raise ValueError(f"Цель {purpose} ссылается на неизвестные продукты {unknown}")
        return self
