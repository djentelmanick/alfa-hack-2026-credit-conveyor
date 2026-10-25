import logging

from aio_pika import Message

from app.domain.events import ApplicationEvent
from app.domain.ports import StatusPublisher
from app.infra.messaging.codec import CONTENT_TYPE, encode_event
from app.infra.messaging.connection import RabbitConnection
from app.infra.messaging.task_queue import PUBLISH_TIMEOUT_SEC
from app.infra.messaging.topology import declare_events_exchange

logger = logging.getLogger(__name__)


class RabbitStatusPublisher(StatusPublisher):
    """Публикует события в fanout applications.events, их читают все процессы api."""

    def __init__(self, connection: RabbitConnection) -> None:
        self._connection = connection

    async def publish(self, event: ApplicationEvent) -> None:
        try:
            channel = await self._connection.publish_channel()
            exchange = await declare_events_exchange(channel)
            await exchange.publish(
                Message(encode_event(event), content_type=CONTENT_TYPE),
                routing_key="",
                timeout=PUBLISH_TIMEOUT_SEC,
            )
        except Exception:
            # Commit уже был: потерянное событие клиент восстановит по snapshot при переподключении.
            logger.exception("Не удалось опубликовать событие по заявке %s", event.application_id)
