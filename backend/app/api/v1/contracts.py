from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import Claims, ClientClaims, get_contract_service
from app.api.v1.schemas.application import FileUrlOut
from app.api.v1.schemas.common import ROLE_ERRORS
from app.api.v1.schemas.contract import (
    ContractIdIn,
    ContractListIn,
    ContractListItemOut,
    ContractOperationsIn,
    ContractOut,
    ContractVersionIn,
    ContractVersionOut,
    OperationPageOut,
    RepayIn,
    ScheduleItemOut,
    TrancheDrawIn,
    TrancheOut,
    TrancheRepayIn,
)
from app.api.v1.schemas.errors import ErrorOut
from app.application.contract_service import ContractService

router = APIRouter(prefix="/contracts", tags=["contracts"])

Contracts = Annotated[ContractService, Depends(get_contract_service)]

_ERRORS: dict[int | str, dict[str, object]] = {**ROLE_ERRORS, 404: {"model": ErrorOut}}
_OPERATION_ERRORS: dict[int | str, dict[str, object]] = {**_ERRORS, 409: {"model": ErrorOut}}


@router.post("/list", summary="Договоры", responses=ROLE_ERRORS)
async def list_contracts(
    body: ContractListIn, claims: Claims, contracts: Contracts
) -> list[ContractListItemOut]:
    found = await contracts.list_for(claims, body.application_id)
    return [ContractListItemOut.from_domain(c) for c in found]


@router.post(
    "/get", summary="Страница договора", responses=_ERRORS, response_model_exclude_none=True
)
async def get(body: ContractIdIn, claims: Claims, contracts: Contracts) -> ContractOut:
    return ContractOut.from_view(await contracts.get(claims, body.contract_id))


@router.post("/schedule", summary="График платежей версии договора", responses=_ERRORS)
async def schedule(
    body: ContractVersionIn, claims: Claims, contracts: Contracts
) -> list[ScheduleItemOut]:
    items = await contracts.schedule(claims, body.contract_id, body.version_no)
    return [ScheduleItemOut.from_domain(n, item) for n, item in enumerate(items, start=1)]


@router.post(
    "/operations",
    summary="Журнал операций",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def operations(
    body: ContractOperationsIn, claims: Claims, contracts: Contracts
) -> OperationPageOut:
    page = await contracts.operations(claims, body.contract_id, body.page, body.size)
    return OperationPageOut.from_domain(page)


@router.post(
    "/versions",
    summary="Версии договора",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def versions(
    body: ContractIdIn, claims: Claims, contracts: Contracts
) -> list[ContractVersionOut]:
    return [
        ContractVersionOut.from_view(v) for v in await contracts.versions(claims, body.contract_id)
    ]


@router.post(
    "/document-url",
    summary="Ссылка на PDF версии договора на 5 минут",
    description="Без version_no отдаётся последняя версия.",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def document_url(body: ContractVersionIn, claims: Claims, contracts: Contracts) -> FileUrlOut:
    link = await contracts.document_url(claims, body.contract_id, body.version_no)
    return FileUrlOut.from_domain(link)


@router.post(
    "/tranches/draw",
    status_code=status.HTTP_201_CREATED,
    summary="Взять транш по кредитной линии",
    responses=_OPERATION_ERRORS,
)
async def draw_tranche(
    body: TrancheDrawIn, claims: ClientClaims, contracts: Contracts
) -> TrancheOut:
    tranche = await contracts.draw_tranche(claims, body.contract_id, body.money)
    return TrancheOut.from_domain(tranche)


@router.post(
    "/tranches/repay",
    summary="Досрочно погасить транш",
    responses=_OPERATION_ERRORS,
    response_model_exclude_none=True,
)
async def repay_tranche(
    body: TrancheRepayIn, claims: ClientClaims, contracts: Contracts
) -> ContractOut:
    view = await contracts.repay_tranche(claims, body.contract_id, body.tranche_id, body.money)
    return ContractOut.from_view(view)


@router.post(
    "/repay",
    summary="Частичное или полное досрочное погашение",
    description="PARTIAL только у кредита одной суммой, создаёт новую версию договора с PDF.",
    responses={**_OPERATION_ERRORS, 503: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def repay(body: RepayIn, claims: ClientClaims, contracts: Contracts) -> ContractOut:
    view = await contracts.repay(claims, body.contract_id, body.kind, body.money)
    return ContractOut.from_view(view)
