"""Фейки фоновых задач и публикации событий для тестов без event loop приложения."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from app.domain.events import ApplicationEvent, StatusChangedEvent
from app.domain.ports import PipelineLock, PipelineTask, StatusPublisher, TaskQueue
from tests.fakes.uow import InMemoryStore


class RecordingTaskQueue(TaskQueue):
    """Запоминает задачи, а запускает их только тест: так видно, что именно поставлено в очередь."""

    def __init__(self) -> None:
        self.tasks: list[PipelineTask] = []

    async def enqueue(self, task: PipelineTask) -> None:
        self.tasks.append(task)

    async def run_all(self, run: Callable[[UUID, UUID], Awaitable[None]]) -> None:
        while self.tasks:
            task = self.tasks.pop(0)
            await run(task.application_id, task.run_id)


class FailingTaskQueue(TaskQueue):
    """Брокер недоступен."""

    async def enqueue(self, task: PipelineTask) -> None:
        raise ConnectionError("RabbitMQ недоступен")


class InMemoryPipelineLock(PipelineLock):
    def __init__(self) -> None:
        self.held: set[UUID] = set()

    def hold(self, application_id: UUID) -> AbstractAsyncContextManager[bool]:
        return self._hold(application_id)

    @asynccontextmanager
    async def _hold(self, application_id: UUID) -> AsyncIterator[bool]:
        if application_id in self.held:
            yield False
            return
        self.held.add(application_id)
        try:
            yield True
        finally:
            self.held.discard(application_id)


class InMemoryEventBus(StatusPublisher):
    """Шина событий вместо fanout RabbitMQ: событие получают все подписчики процесса."""

    def __init__(self) -> None:
        self._sinks: list[Callable[[ApplicationEvent], Awaitable[None]]] = []

    def subscribe(self, sink: Callable[[ApplicationEvent], Awaitable[None]]) -> None:
        self._sinks.append(sink)

    async def publish(self, event: ApplicationEvent) -> None:
        for sink in list(self._sinks):
            await sink(event)


class InProcessTaskQueue(TaskQueue):
    """Очередь, которая сразу запускает обработчик worker фоновой задачей в том же процессе."""

    def __init__(self) -> None:
        self._run: Callable[[UUID, UUID], Awaitable[None]] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def bind(self, run: Callable[[UUID, UUID], Awaitable[None]]) -> None:
        self._run = run

    async def enqueue(self, task: PipelineTask) -> None:
        if self._run is None:
            raise RuntimeError("Обработчик worker не подключён")
        job = asyncio.create_task(self._run(task.application_id, task.run_id))
        self._tasks.add(job)
        job.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for job in list(self._tasks):
            job.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


class RecordingPublisher(StatusPublisher):
    """Запоминает события и число коммитов в хранилище на момент публикации."""

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._store = store
        self.events: list[ApplicationEvent] = []
        self.commits_at_publish: list[int] = []

    async def publish(self, event: ApplicationEvent) -> None:
        self.events.append(event)
        self.commits_at_publish.append(self._store.commits if self._store else 0)

    def statuses(self) -> list[str]:
        return [e.to_status for e in self.events if isinstance(e, StatusChangedEvent)]


class NoopLifecycle:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass
