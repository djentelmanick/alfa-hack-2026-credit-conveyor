from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.deps import Claims, get_emulation_service
from app.api.v1.schemas.common import ROLE_ERRORS
from app.api.v1.schemas.contract import ContractIdIn, ContractOut, EmulationResultOut, RewindIn
from app.api.v1.schemas.errors import ErrorOut
from app.application.emulation_service import EmulationService

router = APIRouter(prefix="/contracts/emulation", tags=["emulation"])

Emulation = Annotated[EmulationService, Depends(get_emulation_service)]

_ERRORS: dict[int | str, dict[str, object]] = {
    **ROLE_ERRORS,
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
}
_DESCRIPTION = "Только при DEMO_MODE=true, иначе 404. Двигает дату только этого договора."


@router.post(
    "/next-month",
    summary="Сдвинуть дату договора на месяц",
    description=_DESCRIPTION,
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def next_month(
    body: ContractIdIn, claims: Claims, emulation: Emulation
) -> EmulationResultOut:
    return EmulationResultOut.from_domain(await emulation.next_month(claims, body.contract_id))


@router.post(
    "/rewind",
    summary="Перемотать договор до даты",
    description=_DESCRIPTION,
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def rewind(body: RewindIn, claims: Claims, emulation: Emulation) -> EmulationResultOut:
    result = await emulation.rewind_to(claims, body.contract_id, body.target_date)
    return EmulationResultOut.from_domain(result)


@router.post(
    "/reset",
    summary="Вернуть договор в состояние сразу после выдачи",
    description=_DESCRIPTION,
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def reset(body: ContractIdIn, claims: Claims, emulation: Emulation) -> ContractOut:
    return ContractOut.from_view(await emulation.reset(claims, body.contract_id))
