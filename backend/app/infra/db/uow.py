from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import LoginTaken
from app.domain.ports import UnitOfWork
from app.infra.db.repositories import (
    SqlApplicationRepository,
    SqlAuditLogRepository,
    SqlConsentRepository,
    SqlContractRepository,
    SqlUserRepository,
)

LOGIN_UNIQUE_CONSTRAINT = "uq_users_login"


class SqlUnitOfWork(UnitOfWork):
    """Одна сессия и одна транзакция на сценарий. Используется только через `async with`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = self._session_factory()
        self._session = session
        self._users = SqlUserRepository(session)
        self._applications = SqlApplicationRepository(session)
        self._contracts = SqlContractRepository(session)
        self.users = self._users
        self.applications = self._applications
        self.contracts = self._contracts
        self.consents = SqlConsentRepository(session)
        self.audit = SqlAuditLogRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await super().__aexit__(exc_type, exc, tb)
        finally:
            await self._active_session().close()
            self._session = None

    async def commit(self) -> None:
        session = self._active_session()
        trackers = (self._users.tracked, self._applications.tracked, self._contracts.tracked)
        try:
            for tracked in trackers:
                await tracked.flush()
            await session.commit()
        except IntegrityError as exc:
            # Два одновременных register с одним логином: проверку в сервисе прошли оба.
            if LOGIN_UNIQUE_CONSTRAINT in str(exc.orig):
                raise LoginTaken() from exc
            raise
        for tracked in trackers:
            tracked.committed()

    async def rollback(self) -> None:
        await self._active_session().rollback()

    def _active_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("SqlUnitOfWork используется вне `async with`")
        return self._session
