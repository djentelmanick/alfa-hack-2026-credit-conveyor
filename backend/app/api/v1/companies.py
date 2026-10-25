from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import Claims, ClientClaims, get_company_service, get_consent_service
from app.api.v1.schemas.common import ROLE_ERRORS
from app.api.v1.schemas.company import AuthorityIn, CompanyOut, ConsentGiveIn, ConsentOut, InnIn
from app.api.v1.schemas.errors import ErrorOut
from app.application.company_service import CompanyService
from app.application.consent_service import ConsentService

router = APIRouter(tags=["companies"])

Companies = Annotated[CompanyService, Depends(get_company_service)]
Consents = Annotated[ConsentService, Depends(get_consent_service)]


@router.post(
    "/companies/get",
    summary="Компания по ИНН из ЕГРЮЛ/ЕГРИП",
    description=(
        "Доступно клиенту и аналитику. has_account и annual_revenue приходят только "
        "руководителю компании и аналитику."
    ),
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}, 503: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def get_company(body: InnIn, claims: Claims, companies: Companies) -> CompanyOut:
    return CompanyOut.from_domain(await companies.get(claims, body.inn))


@router.post(
    "/companies/authority",
    summary="Подтвердить полномочия представителя по доверенности",
    description=(
        "Директор и ИП проходят по ЕГРЮЛ/ЕГРИП без доверенности. Остальные прикладывают МЧД "
        "или нотариальную доверенность; после проверки компания привязывается к пользователю."
    ),
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}, 503: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def claim_authority(
    body: AuthorityIn, claims: ClientClaims, companies: Companies
) -> CompanyOut:
    return CompanyOut.from_domain(
        await companies.claim_authority(claims, body.inn, body.to_claim())
    )


@router.post(
    "/consents/give",
    status_code=status.HTTP_201_CREATED,
    summary="Дать согласия по компании",
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}, 422: {"model": ErrorOut}},
)
async def give_consents(
    body: ConsentGiveIn, claims: ClientClaims, consents: Consents
) -> list[ConsentOut]:
    given = await consents.give(claims, body.inn, body.types)
    return [ConsentOut.from_domain(c) for c in given]


@router.post("/consents/list", summary="Согласия по компании", responses=ROLE_ERRORS)
async def list_consents(body: InnIn, claims: ClientClaims, consents: Consents) -> list[ConsentOut]:
    return [ConsentOut.from_domain(c) for c in await consents.list_for(claims, body.inn)]
