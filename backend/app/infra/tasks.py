import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.domain.ports import TaskRunner

logger = logging.getLogger(__name__)

PERIODIC_TASK = "periodic"


class AsyncioTaskRunner(TaskRunner):
    """Фоновые задачи в том же процессе (BACKEND_SPEC, раздел 11).

    Ссылки на задачи храним сами: asyncio держит только слабые, и задачу мог бы собрать GC.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def enqueue[*Ts](self, job: Callable[[*Ts], Awaitable[None]], *args: *Ts) -> None:
        self._track(asyncio.create_task(self._guarded(job, *args)))

    def every(self, interval_sec: float, job: Callable[[], Awaitable[object]]) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_sec)
                try:
                    await job()
                except Exception:
                    logger.exception("Периодическая задача %s упала", _name(job))

        self._track(asyncio.create_task(loop(), name=PERIODIC_TASK))

    async def drain(self) -> None:
        """Дождаться всех разовых задач. Нужен тестам, периодические задачи сюда не входят."""
        while pending := [t for t in self._tasks if not t.done() and t.get_name() != PERIODIC_TASK]:
            await asyncio.gather(*pending, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guarded[*Ts](job: Callable[[*Ts], Awaitable[None]], *args: *Ts) -> None:
        try:
            await job(*args)
        except Exception:
            logger.exception("Фоновая задача %s%r упала", _name(job), args)


def _name(job: Callable[..., object]) -> str:
    return getattr(job, "__qualname__", repr(job))
