from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import AnalystClaims, get_analyst_service, get_contract_service
from app.api.v1.schemas.analyst import (
    AnalystListIn,
    AnalystPageOut,
    ApplicationReportOut,
    ApproveIn,
    CounterOfferIn,
    RecheckIn,
    RecheckOut,
    RejectIn,
    RequestInfoIn,
)
from app.api.v1.schemas.application import ApplicationIdIn, ApplicationOut
from app.api.v1.schemas.common import ROLE_ERRORS
from app.api.v1.schemas.contract import ContractOut, ContractUpdateIn
from app.api.v1.schemas.errors import ErrorOut
from app.application.analyst_service import AnalystService
from app.application.application_service import ApplicationService
from app.application.contract_service import ContractService

router = APIRouter(prefix="/analyst/applications", tags=["analyst"])
contracts_router = APIRouter(prefix="/analyst/contracts", tags=["analyst"])

Analyst = Annotated[AnalystService, Depends(get_analyst_service)]
Contracts = Annotated[ContractService, Depends(get_contract_service)]

_ERRORS: dict[int | str, dict[str, object]] = {
    **ROLE_ERRORS,
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    422: {"model": ErrorOut},
}


@router.post(
    "/list",
    summary="Очередь и список заявок для аналитика",
    responses=ROLE_ERRORS,
    response_model_exclude_none=True,
)
async def list_applications(
    body: AnalystListIn, _: AnalystClaims, analyst: Analyst
) -> AnalystPageOut:
    return AnalystPageOut.from_domain(await analyst.queue(body.to_filter(), body.page, body.size))


@router.post(
    "/report",
    summary="Отчёт по заявке из 7 разделов",
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def report(
    body: ApplicationIdIn, claims: AnalystClaims, analyst: Analyst
) -> ApplicationReportOut:
    return ApplicationReportOut.from_report(await analyst.report(body.application_id), claims)


@router.post(
    "/approve",
    summary="Одобрить со ставкой в диапазоне варианта",
    description="Только когда все источники ответили, иначе 409 SOURCES_NOT_READY.",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def approve(body: ApproveIn, claims: AnalystClaims, analyst: Analyst) -> ApplicationOut:
    app = await analyst.approve(claims, body.application_id, body.rate, body.comment)
    return ApplicationOut.from_view(ApplicationService.view(app, claims))


@router.post(
    "/counter-offer",
    summary="Предложить другие условия",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def counter_offer(
    body: CounterOfferIn, claims: AnalystClaims, analyst: Analyst
) -> ApplicationOut:
    app = await analyst.counter_offer(
        claims, body.application_id, [o.to_terms() for o in body.options], body.comment
    )
    return ApplicationOut.from_view(ApplicationService.view(app, claims))


@router.post(
    "/request-info",
    summary="Запросить у клиента данные",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def request_info(
    body: RequestInfoIn, claims: AnalystClaims, analyst: Analyst
) -> ApplicationOut:
    app = await analyst.request_info(claims, body.application_id, body.to_items(), body.comment)
    return ApplicationOut.from_view(ApplicationService.view(app, claims))


@router.post("/reject", summary="Отказать", responses=_ERRORS, response_model_exclude_none=True)
async def reject(body: RejectIn, claims: AnalystClaims, analyst: Analyst) -> ApplicationOut:
    app = await analyst.reject(claims, body.application_id, body.reason_code, body.comment)
    return ApplicationOut.from_view(ApplicationService.view(app, claims))


@router.post(
    "/recheck",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Повторить проверки источников",
    description="Статусы конвейера приходят по WebSocket /ws/applications.",
    responses=_ERRORS,
)
async def recheck(body: RecheckIn, claims: AnalystClaims, analyst: Analyst) -> RecheckOut:
    app = await analyst.recheck(claims, body.application_id, body.comment)
    return RecheckOut(application_id=app.id, status="CHECKING")


@contracts_router.post(
    "/update",
    summary="Изменить ставку или срок договора",
    description="Создаёт новую версию договора с новым PDF. Сумму и лимит менять нельзя.",
    responses={**_ERRORS, 503: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def update_contract(
    body: ContractUpdateIn, claims: AnalystClaims, analyst: Analyst, contracts: Contracts
) -> ContractOut:
    contract = await analyst.edit_contract(
        claims, body.contract_id, body.rate, body.term_months, body.comment
    )
    return ContractOut.from_view(contracts.view(contract))
