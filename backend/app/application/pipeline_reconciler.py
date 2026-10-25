"""Заявки не зависают, даже если задача не дошла до очереди (BACKEND_SPEC, раздел 11)."""

import logging
from collections.abc import Callable
from datetime import timedelta

from app.application.pipeline import PIPELINE_STATUSES
from app.domain.ports import Clock, PipelineTask, TaskQueue, UnitOfWork

logger = logging.getLogger(__name__)


class PipelineReconciler:
    """Ставит заново задачи заявок, которые слишком долго стоят в статусах конвейера.

    Outbox не нужен: лишняя задача безопасна, потому что PipelineOrchestrator.run идемпотентен.
    """

    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        queue: TaskQueue,
        clock: Clock,
        stale_after: timedelta,
    ) -> None:
        self._uow = uow
        self._queue = queue
        self._clock = clock
        self._stale_after = stale_after

    async def run_once(self) -> int:
        async with self._uow() as uow:
            stale = await uow.applications.list_stale(
                PIPELINE_STATUSES, self._clock.now() - self._stale_after
            )
        queued = 0
        for app in stale:
            if app.pipeline_run_id is None:
                logger.warning("У зависшей заявки %s нет pipeline_run_id", app.id)
                continue
            logger.info("Заявка %s зависла в статусе %s, ставим заново", app.id, app.status)
            await self._queue.enqueue(
                PipelineTask(application_id=app.id, run_id=app.pipeline_run_id)
            )
            queued += 1
        return queued
