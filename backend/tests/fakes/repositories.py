from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from app.domain.entities import Application
from app.domain.enums import ApplicationStatus
from app.domain.errors import NotFound
from app.domain.ports import AnalystFilter, ApplicationRepository
from app.domain.state_machine import OFFER_STATUSES


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self, *applications: Application) -> None:
        self._items: dict[UUID, Application] = {a.id: a for a in applications}

    async def get(self, application_id: UUID) -> Application:
        try:
            return self._items[application_id]
        except KeyError:
            raise NotFound("Заявка не найдена") from None

    async def add(self, application: Application) -> None:
        self._items[application.id] = application

    async def find_active_by_inn(self, inn: str) -> Application | None:
        return next((a for a in self._items.values() if a.inn == inn and a.is_active()), None)

    async def list_for_applicant(
        self,
        applicant_id: UUID,
        *,
        status: ApplicationStatus | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Application], int]:
        found = sorted(
            (
                a
                for a in self._items.values()
                if a.applicant_id == applicant_id and (status is None or a.status is status)
            ),
            key=lambda a: a.created_at,
            reverse=True,
        )
        end = None if limit is None else offset + limit
        return found[offset:end], len(found)

    async def list_by_statuses(self, statuses: Collection[ApplicationStatus]) -> list[Application]:
        return sorted(
            (a for a in self._items.values() if a.status in statuses), key=lambda a: a.created_at
        )

    async def list_expired(self, now: datetime) -> list[Application]:
        return [
            a
            for a in self._items.values()
            if a.status in OFFER_STATUSES and a.expires_at is not None and a.expires_at <= now
        ]

    async def list_for_analyst(
        self, flt: AnalystFilter, *, offset: int = 0, limit: int | None = None
    ) -> tuple[list[Application], int]:
        found = [
            a
            for a in self._items.values()
            if a.status is not ApplicationStatus.DRAFT
            and (not flt.manual_queue or a.status is ApplicationStatus.MANUAL_REVIEW)
            and (flt.status is None or a.status is flt.status)
            and (flt.inn is None or a.inn == flt.inn)
            and (
                flt.risk_class is None
                or (a.scoring is not None and a.scoring.risk_class is flt.risk_class)
            )
        ]
        if flt.manual_queue:
            found.sort(key=lambda a: a.created_at)
        else:
            found.sort(key=lambda a: a.updated_at, reverse=True)
        end = None if limit is None else offset + limit
        return found[offset:end], len(found)
