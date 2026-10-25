from aio_pika import DeliveryMode, Message

from app.domain.ports import PipelineTask, TaskQueue
from app.infra.messaging.codec import CONTENT_TYPE, encode_task
from app.infra.messaging.connection import RabbitConnection
from app.infra.messaging.topology import PROCESS_QUEUE

# Пока брокер недоступен, robust-канал ждёт переподключения: без таймаута submit завис бы.
PUBLISH_TIMEOUT_SEC = 5.0


class RabbitTaskQueue(TaskQueue):
    def __init__(self, connection: RabbitConnection) -> None:
        self._connection = connection

    async def enqueue(self, task: PipelineTask) -> None:
        channel = await self._connection.publish_channel()
        await channel.default_exchange.publish(
            Message(
                encode_task(task),
                content_type=CONTENT_TYPE,
                delivery_mode=DeliveryMode.PERSISTENT,
            ),
            routing_key=PROCESS_QUEUE,
            timeout=PUBLISH_TIMEOUT_SEC,
        )
