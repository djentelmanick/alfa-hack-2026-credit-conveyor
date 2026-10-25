"""Фейки фоновых задач и публикации событий для тестов без event loop приложения."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.events import ApplicationEvent, StatusChangedEvent
from app.domain.ports import StatusPublisher, TaskRunner
from tests.fakes.uow import InMemoryStore


class RecordingTaskRunner(TaskRunner):
    """Запоминает задачи, а запускает их только тест: так видно, что именно поставлено в очередь."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Callable[..., Awaitable[None]], tuple[Any, ...]]] = []

    def enqueue[*Ts](self, job: Callable[[*Ts], Awaitable[None]], *args: *Ts) -> None:
        self.jobs.append((job, tuple(args)))

    async def run_all(self) -> None:
        while self.jobs:
            job, args = self.jobs.pop(0)
            await job(*args)


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
