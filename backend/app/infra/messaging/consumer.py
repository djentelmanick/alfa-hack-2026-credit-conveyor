import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractQueue

from app.application.log_context import bind_application
from app.domain.errors import NotFound
from app.infra.messaging.codec import MalformedMessageError, decode_task
from app.infra.messaging.connection import RabbitConnection
from app.infra.messaging.topology import DELIVERY_LIMIT, declare_process_queue

logger = logging.getLogger(__name__)

type PipelineRun = Callable[[UUID, UUID], Awaitable[None]]

# Пауза перед возвратом упавшей задачи в очередь, иначе она крутилась бы без передышки.
RETRY_PAUSE_SEC = 2.0


class PipelineConsumer:
    """Читает applications.process в worker. ack только после того, как прогон закоммичен."""

    def __init__(
        self,
        connection: RabbitConnection,
        run: PipelineRun,
        give_up: PipelineRun,
        *,
        prefetch: int,
    ) -> None:
        self._connection = connection
        self._run = run
        self._give_up = give_up
        self._prefetch = prefetch
        self._channel: AbstractChannel | None = None
        self._queue: AbstractQueue | None = None
        self._consumer_tag: str | None = None

    async def start(self) -> None:
        self._channel = await self._connection.channel(prefetch=self._prefetch)
        self._queue = await declare_process_queue(self._channel)
        self._consumer_tag = await self._queue.consume(self._on_message)

    async def stop(self) -> None:
        if self._queue is not None and self._consumer_tag is not None:
            await self._queue.cancel(self._consumer_tag)
        if self._channel is not None:
            # Неподтверждённые задачи брокер доставит заново: прогон идемпотентен.
            await self._channel.close()
        self._channel = self._queue = self._consumer_tag = None

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        try:
            task = decode_task(message.body)
        except MalformedMessageError:
            logger.exception("Битая задача конвейера, отправляем в DLQ")
            await message.reject(requeue=False)
            return

        with bind_application(task.application_id):
            try:
                await self._run(task.application_id, task.run_id)
            except NotFound:
                logger.warning("Заявки %s нет, задача уходит в DLQ", task.application_id)
                await message.reject(requeue=False)
                return
            except Exception:
                logger.exception("Прогон заявки %s упал", task.application_id)
                await self._retry_or_give_up(message, task.application_id, task.run_id)
                return
            await message.ack()

    async def _retry_or_give_up(
        self, message: AbstractIncomingMessage, application_id: UUID, run_id: UUID
    ) -> None:
        # Без этого задача с ошибкой, повторяющейся при каждой доставке, крутилась бы вечно,
        # а заявка висела бы в CHECKING.
        if _previous_attempts(message) + 1 < DELIVERY_LIMIT:
            await asyncio.sleep(RETRY_PAUSE_SEC)
            await message.nack(requeue=True)
            return
        try:
            await self._give_up(application_id, run_id)
        except Exception:
            # Например, БД недоступна: задача уходит в DLQ, заявку потом подхватит reconciler.
            logger.exception("Не удалось передать заявку %s аналитику", application_id)
            await message.reject(requeue=False)
            return
        await message.ack()


def _previous_attempts(message: AbstractIncomingMessage) -> int:
    # В RabbitMQ 4 nack с requeue увеличивает только x-acquired-count; x-delivery-count
    # и x-delivery-limit считают лишь аварийные возвраты (обрыв канала), поэтому берём больший.
    headers = message.headers or {}
    counts = [headers.get("x-acquired-count", 0), headers.get("x-delivery-count", 0)]
    return max(c for c in counts if isinstance(c, int))
