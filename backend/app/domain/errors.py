from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar


class DomainError(Exception):
    """Наследники задают code и http_status по таблице BACKEND_SPEC, раздел 18."""

    code: ClassVar[str] = "INTERNAL_ERROR"
    http_status: ClassVar[int] = 500
    default_message: ClassVar[str] = "Внутренняя ошибка"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: str
    message: str


class ValidationFailed(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 422
    default_message = "Проверьте поля формы"

    def __init__(self, details: Sequence[FieldError], message: str | None = None) -> None:
        super().__init__(message)
        self.details: tuple[FieldError, ...] = tuple(details)


class TokenMissing(DomainError):
    code = "TOKEN_MISSING"
    http_status = 401
    default_message = "Требуется авторизация"


class TokenInvalid(DomainError):
    code = "TOKEN_INVALID"
    http_status = 401
    default_message = "Токен недействителен"


class TokenExpired(DomainError):
    code = "TOKEN_EXPIRED"
    http_status = 401
    default_message = "Срок действия токена истёк"


class InvalidCredentials(DomainError):
    code = "INVALID_CREDENTIALS"
    http_status = 401
    default_message = "Неверный логин или пароль"


class LoginTaken(DomainError):
    code = "LOGIN_TAKEN"
    http_status = 409
    default_message = "Этот логин уже занят"


class Forbidden(DomainError):
    code = "FORBIDDEN"
    http_status = 403
    default_message = "Недостаточно прав"


class NotDirector(DomainError):
    code = "NOT_DIRECTOR"
    http_status = 403
    default_message = "Подать заявку может только руководитель компании или сам ИП"


class NotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404
    default_message = "Не найдено"


class InvalidStatus(DomainError):
    code = "INVALID_STATUS"
    http_status = 409
    default_message = "Действие недоступно в текущем статусе"


class ActiveApplicationExists(DomainError):
    code = "ACTIVE_APPLICATION_EXISTS"
    http_status = 409
    default_message = "По этому ИНН уже есть активная заявка"


class SourcesNotReady(DomainError):
    code = "SOURCES_NOT_READY"
    http_status = 409
    default_message = "Не все источники ответили, нужна повторная проверка"


class OfferExpired(DomainError):
    code = "OFFER_EXPIRED"
    http_status = 410
    default_message = "Срок предложения истёк"


class BadSigningCode(DomainError):
    code = "BAD_SIGNING_CODE"
    http_status = 400
    default_message = "Неверный код подтверждения"


class FileTooLarge(DomainError):
    code = "FILE_TOO_LARGE"
    http_status = 413
    default_message = "Файл слишком большой"


class SourceUnavailable(DomainError):
    code = "SOURCE_UNAVAILABLE"
    http_status = 503
    default_message = "Внешний источник не отвечает"


class StorageUnavailable(DomainError):
    code = "STORAGE_UNAVAILABLE"
    http_status = 503
    default_message = "Хранилище файлов недоступно"


class ScoringNotImplemented(NotImplementedError):
    """Модель скоринга ещё не готова: конвейер отправляет заявку на ручную проверку.

    Не наследник DomainError, потому что это не ошибка запроса клиента и в HTTP не уходит.
    """

    def __init__(self) -> None:
        super().__init__("Модель скоринга ещё не реализована")
