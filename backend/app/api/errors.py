import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.schemas.errors import ErrorBody, ErrorDetail, ErrorOut
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

# Ошибки уровня Starlette (нет маршрута, не тот метод) тоже обязаны приходить в формате ErrorOut.
_HTTP_STATUS_CODES = {
    404: ("NOT_FOUND", "Не найдено"),
    405: ("METHOD_NOT_ALLOWED", "Метод не поддерживается"),
}


def _error_response(
    status_code: int, code: str, message: str, details: list[ErrorDetail] | None = None
) -> JSONResponse:
    body = ErrorOut(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def _field_path(loc: tuple[int | str, ...]) -> str:
    # Первый элемент loc это источник ("body"), фронтенду нужен только путь к полю.
    parts = loc[1:] if loc and loc[0] == "body" else loc
    return ".".join(str(part) for part in parts) or "body"


async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return _error_response(exc.http_status, exc.code, exc.message)


async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        ErrorDetail(
            field=_field_path(tuple(err.get("loc", ()))),
            code=str(err.get("type", "invalid")).upper(),
            message=str(err.get("msg", "")),
        )
        for err in exc.errors()
    ]
    return _error_response(422, "VALIDATION_ERROR", "Проверьте поля формы", details)


async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_STATUS_CODES.get(exc.status_code, ("HTTP_ERROR", str(exc.detail)))
    return _error_response(exc.status_code, code, message)


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return _error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка")


def _narrowed[E: Exception](
    exc_type: type[E], handler: Callable[[Request, E], Awaitable[JSONResponse]]
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    # Starlette типизирует параметр обработчика как Exception, поэтому сужаем тип здесь.
    async def wrapper(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, exc_type):
            raise exc
        return await handler(request, exc)

    return wrapper


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _narrowed(DomainError, _domain_error_handler))
    app.add_exception_handler(
        RequestValidationError, _narrowed(RequestValidationError, _validation_error_handler)
    )
    app.add_exception_handler(
        StarletteHTTPException, _narrowed(StarletteHTTPException, _http_error_handler)
    )
    app.add_exception_handler(Exception, _unhandled_error_handler)
