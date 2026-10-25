from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.ports import PipelineLock


class PgAdvisoryPipelineLock(PipelineLock):
    """Сессионный advisory lock PostgreSQL на время прогона.

    Держится на отдельном соединении, а не в транзакции шага: прогон состоит из нескольких
    коммитов. Если worker упадёт, соединение закроется и PostgreSQL снимет блокировку сам.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    def hold(self, application_id: UUID) -> AbstractAsyncContextManager[bool]:
        return self._hold(application_id)

    @asynccontextmanager
    async def _hold(self, application_id: UUID) -> AsyncIterator[bool]:
        key = _lock_key(application_id)
        async with self._engine.connect() as conn:
            acquired = bool(
                await conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            )
            # Без commit соединение вернулось бы в пул с открытой транзакцией.
            await conn.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await conn.commit()


def _lock_key(application_id: UUID) -> int:
    # Ключ advisory lock это bigint: берём первые 8 байт UUID, коллизия лишь притормозит прогон.
    return int.from_bytes(application_id.bytes[:8], "big", signed=True)
