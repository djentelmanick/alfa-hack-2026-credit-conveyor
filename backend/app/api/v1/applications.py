from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from app.api.v1.deps import Claims, ClientClaims, get_application_service, get_signing_service
from app.api.v1.schemas.application import (
    AcceptIn,
    ApplicationCreateIn,
    ApplicationFindingsOut,
    ApplicationIdIn,
    ApplicationListIn,
    ApplicationOut,
    ApplicationPageOut,
    ApplicationUpdateIn,
    CancelIn,
    ClientDecisionOut,
    FileUrlOut,
    StatementIdIn,
    StatementOut,
    StatusTransitionOut,
    SubmitOut,
    ValidateOut,
)
from app.api.v1.schemas.common import ROLE_ERRORS
from app.api.v1.schemas.contract import SigningCodeOut, SigningConfirmIn, SigningConfirmOut
from app.api.v1.schemas.errors import ErrorOut
from app.application.application_service import ApplicationService, UploadedFile
from app.application.signing_service import SigningService

router = APIRouter(prefix="/applications", tags=["applications"])

Applications = Annotated[ApplicationService, Depends(get_application_service)]
Signing = Annotated[SigningService, Depends(get_signing_service)]

_ERRORS: dict[int | str, dict[str, object]] = {
    **ROLE_ERRORS,
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    422: {"model": ErrorOut},
}
_READ_ERRORS: dict[int | str, dict[str, object]] = {**ROLE_ERRORS, 404: {"model": ErrorOut}}


@router.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    summary="Создать черновик заявки",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def create(
    body: ApplicationCreateIn, claims: ClientClaims, applications: Applications
) -> ApplicationOut:
    app = await applications.create_draft(claims, body.inn, body.to_form())
    return ApplicationOut.from_view(applications.view(app, claims))


@router.post("/list", summary="Заявки клиента", responses=ROLE_ERRORS)
async def list_applications(
    body: ApplicationListIn, claims: ClientClaims, applications: Applications
) -> ApplicationPageOut:
    page = await applications.list_for_client(claims, body.status, body.page, body.size)
    return ApplicationPageOut.from_domain(page)


@router.post(
    "/get", summary="Карточка заявки", responses=_READ_ERRORS, response_model_exclude_none=True
)
async def get(
    body: ApplicationIdIn, claims: ClientClaims, applications: Applications
) -> ApplicationOut:
    return ApplicationOut.from_view(await applications.get_for_client(claims, body.application_id))


@router.post(
    "/update",
    summary="Изменить анкету в DRAFT или NEEDS_INFO",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def update(
    body: ApplicationUpdateIn, claims: ClientClaims, applications: Applications
) -> ApplicationOut:
    app = await applications.update_draft(claims, body.application_id, body.to_patch())
    return ApplicationOut.from_view(applications.view(app, claims))


@router.post("/validate", summary="Проверить анкету без отправки", responses=_READ_ERRORS)
async def validate(
    body: ApplicationIdIn, claims: ClientClaims, applications: Applications
) -> ValidateOut:
    return ValidateOut.from_errors(await applications.validate(claims, body.application_id))


@router.post(
    "/statements/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить выписку 1С из другого банка",
    description="multipart/form-data: application_id и file (1CClientBankExchange, до 10 МБ).",
    responses={**_ERRORS, 413: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
async def upload_statement(
    application_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    claims: ClientClaims,
    applications: Applications,
) -> StatementOut:
    upload = UploadedFile(
        file_name=file.filename or "statement.txt",
        content_type=file.content_type or "text/plain",
        # Весь файл в память не читаем: хвост сверх лимита не нужен, ответ всё равно 413.
        data=await file.read(applications.statement_read_limit),
    )
    statement = await applications.attach_statement(claims, application_id, upload)
    # Выписку чужого ИНН сервис не принимает, поэтому ИНН заявки совпадает с ИНН выписки.
    return StatementOut.from_domain(statement, statement.account_inn)


@router.post(
    "/statements/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить выписку в DRAFT или NEEDS_INFO",
    responses=_ERRORS,
)
async def delete_statement(
    body: StatementIdIn, claims: ClientClaims, applications: Applications
) -> Response:
    await applications.delete_statement(claims, body.application_id, body.statement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/statements/download-url",
    summary="Ссылка на оригинал выписки на 5 минут",
    responses=_ERRORS,
    response_model_exclude_none=True,
)
async def statement_download_url(
    body: StatementIdIn, claims: Claims, applications: Applications
) -> FileUrlOut:
    link = await applications.statement_url(claims, body.application_id, body.statement_id)
    return FileUrlOut.from_domain(link)


@router.post(
    "/submit",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Отправить заявку в конвейер",
    description="Статусы конвейера приходят по WebSocket /ws/applications.",
    responses=_ERRORS,
)
async def submit(
    body: ApplicationIdIn, claims: ClientClaims, applications: Applications
) -> SubmitOut:
    app = await applications.submit(claims, body.application_id)
    return SubmitOut(application_id=app.id, status="SUBMITTED")


@router.post("/history", summary="История статусов", responses=_READ_ERRORS)
async def history(
    body: ApplicationIdIn, claims: Claims, applications: Applications
) -> list[StatusTransitionOut]:
    entries = await applications.history(claims, body.application_id)
    return [StatusTransitionOut.from_entry(e) for e in entries]


@router.post(
    "/decision",
    summary="Решение по заявке",
    responses=_READ_ERRORS,
    response_model_exclude_none=True,
)
async def decision(
    body: ApplicationIdIn, claims: ClientClaims, applications: Applications
) -> ClientDecisionOut:
    return ClientDecisionOut.from_view(await applications.decision(claims, body.application_id))


@router.post(
    "/findings",
    summary="Что банк нашёл по источникам",
    responses={**_READ_ERRORS, 409: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def findings(
    body: ApplicationIdIn, claims: ClientClaims, applications: Applications
) -> ApplicationFindingsOut:
    return ApplicationFindingsOut.from_domain(
        await applications.findings(claims, body.application_id)
    )


@router.post(
    "/accept",
    summary="Принять одобрение или вариант контрпредложения",
    responses={**_ERRORS, 410: {"model": ErrorOut}},
    response_model_exclude_none=True,
)
async def accept(
    body: AcceptIn, claims: ClientClaims, applications: Applications
) -> ApplicationOut:
    app = await applications.accept(claims, body.application_id, body.offer_id)
    return ApplicationOut.from_view(applications.view(app, claims))


@router.post(
    "/cancel", summary="Отменить заявку", responses=_ERRORS, response_model_exclude_none=True
)
async def cancel(
    body: CancelIn, claims: ClientClaims, applications: Applications
) -> ApplicationOut:
    app = await applications.cancel(claims, body.application_id, body.reason)
    return ApplicationOut.from_view(applications.view(app, claims))


@router.post(
    "/signing/code",
    status_code=status.HTTP_201_CREATED,
    summary="Выдать код подписания",
    description="Код показывается прямо в интерфейсе. Новый запрос заменяет прежний код.",
    responses=_ERRORS,
)
async def signing_code(
    body: ApplicationIdIn, claims: ClientClaims, signing: Signing
) -> SigningCodeOut:
    return SigningCodeOut.from_domain(await signing.issue_code(claims, body.application_id))


@router.post(
    "/signing/confirm",
    summary="Подписать договор кодом",
    description="SIGNING в ISSUED. При недоступном S3 выдача откатывается целиком (503).",
    responses={**_ERRORS, 400: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
async def signing_confirm(
    body: SigningConfirmIn, claims: ClientClaims, signing: Signing
) -> SigningConfirmOut:
    contract = await signing.confirm(claims, body.application_id, body.code)
    return SigningConfirmOut(status="ISSUED", contract_id=contract.id)
