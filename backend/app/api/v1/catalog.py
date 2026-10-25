from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.deps import Claims, ClientClaims, get_catalog
from app.api.v1.schemas.catalog import (
    CollateralTypeOut,
    ProductOut,
    PurposeOut,
    RecommendationIn,
    RecommendationOut,
)
from app.api.v1.schemas.common import AUTH_ERRORS, ROLE_ERRORS
from app.domain.enums import ProductCode
from app.domain.products import ProductCatalog

router = APIRouter(tags=["catalog"])

Catalog = Annotated[ProductCatalog, Depends(get_catalog)]


@router.get(
    "/products",
    summary="Продукты и границы условий",
    responses=AUTH_ERRORS,
    response_model_exclude_none=True,
)
async def products(_: Claims, catalog: Catalog) -> list[ProductOut]:
    return [ProductOut.from_domain(p, catalog) for p in catalog.all()]


@router.post("/products/recommendation", summary="Продукт под цель кредита", responses=ROLE_ERRORS)
async def recommendation(
    body: RecommendationIn, _: ClientClaims, catalog: Catalog
) -> RecommendationOut:
    return RecommendationOut.from_domain(catalog.recommend(body.purpose))


@router.get("/dictionaries/purposes", summary="Цели кредита", responses=AUTH_ERRORS)
async def purposes(_: Claims, catalog: Catalog) -> list[PurposeOut]:
    return [
        PurposeOut(
            code=rec.purpose,
            label=rec.label,
            products=[ProductCode(code) for code in rec.allowed],
        )
        for rec in catalog.recommendations()
    ]


@router.get("/dictionaries/collateral-types", summary="Типы залога", responses=AUTH_ERRORS)
async def collateral_types(_: Claims, catalog: Catalog) -> list[CollateralTypeOut]:
    return [
        CollateralTypeOut(code=code, label=label)
        for code, label in catalog.collateral_types().items()
    ]
