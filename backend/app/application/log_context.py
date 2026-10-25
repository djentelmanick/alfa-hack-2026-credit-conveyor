"""application_id в каждой записи лога (CLAUDE.md, «Стиль кода»).

Конвейер и сервисы зовут адаптеры и повторы, которые про заявку не знают. Поэтому id заявки
кладётся в contextvar на время сценария, а фильтр на обработчике дописывает его в запись.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

NO_APPLICATION = "-"

_application_id: ContextVar[str] = ContextVar("application_id", default=NO_APPLICATION)


@contextmanager
def bind_application(application_id: UUID) -> Iterator[None]:
    token = _application_id.set(str(application_id))
    try:
        yield
    finally:
        _application_id.reset(token)


class ApplicationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.application_id = _application_id.get()
        return True
