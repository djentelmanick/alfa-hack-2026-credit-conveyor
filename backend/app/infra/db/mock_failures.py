from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import SourceType
from app.infra.db.models import MockFailureRow
from app.infra.mocks.base import MockFailures


class SqlMockFailures(MockFailures):
    """Поломки моков в PostgreSQL.

    /dev/mocks/failures вызывает api, а источники опрашивает worker: память у них не общая.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self._session_factory = session_factory

    async def set(self, source: SourceType, inn: str, *, enabled: bool) -> None:
        async with self._session_factory.begin() as session:
            if enabled:
                await session.execute(
                    insert(MockFailureRow)
                    .values(source=source.value, inn=inn)
                    .on_conflict_do_nothing()
                )
            else:
                await session.execute(
                    delete(MockFailureRow).where(
                        MockFailureRow.source == source.value, MockFailureRow.inn == inn
                    )
                )

    async def failing_inns(self, source: SourceType) -> frozenset[str]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MockFailureRow.inn).where(MockFailureRow.source == source.value)
            )
            return frozenset(rows)

    async def clear(self) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(delete(MockFailureRow))
