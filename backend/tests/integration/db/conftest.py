"""PostgreSQL для тестов репозиториев. Базу готовит фикстура database_url в tests/conftest.py."""

from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.infra.db.models import Base
from app.infra.db.session import create_session_factory
from app.infra.db.uow import SqlUnitOfWork


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def uow(engine: AsyncEngine) -> Callable[[], SqlUnitOfWork]:
    session_factory = create_session_factory(engine)
    return lambda: SqlUnitOfWork(session_factory)
