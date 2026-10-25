import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class AsyncioTaskRunner:
    """Периодические задачи worker: reconciler и ExpirationJob (BACKEND_SPEC, раздел 11).

    Ссылки на задачи храним сами: asyncio держит только слабые, и задачу мог бы собрать GC.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def every(self, interval_sec: float, job: Callable[[], Awaitable[object]]) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_sec)
                try:
                    await job()
                except Exception:
                    logger.exception("Периодическая задача %s упала", _name(job))

        task = asyncio.create_task(loop())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _name(job: Callable[..., object]) -> str:
    return getattr(job, "__qualname__", repr(job))
