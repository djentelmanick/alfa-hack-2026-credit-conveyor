import logging
from collections.abc import Callable

from app.domain.entities import Application
from app.domain.ports import Clock, StatusPublisher, UnitOfWork

logger = logging.getLogger(__name__)

OFFER_EXPIRED = "OFFER_EXPIRED"


class ExpirationJob:
    """Одобрение и контрпредложение действуют 7 дней, потом заявка отменяется (SCOPE, раздел 8).

    Время берётся из Clock, а не из даты договора: эмуляция времени договора сюда не влияет.
    """

    def __init__(
        self, *, uow: Callable[[], UnitOfWork], publisher: StatusPublisher, clock: Clock
    ) -> None:
        self._uow = uow
        self._publisher = publisher
        self._clock = clock

    async def run_once(self) -> int:
        now = self._clock.now()
        async with self._uow() as uow:
            expired: list[Application] = await uow.applications.list_expired(now)
            for app in expired:
                app.cancel(actor_id=None, reason=OFFER_EXPIRED, now=now)
            await uow.commit()
        for app in expired:
            logger.info("Срок предложения по заявке %s истёк", app.id)
            for event in app.pull_events():
                await self._publisher.publish(event)
        return len(expired)
