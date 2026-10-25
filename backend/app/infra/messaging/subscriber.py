import logging
from collections.abc import Awaitable, Callable

from aio_pika.abc import AbstractChannel, AbstractIncomingMessage

from app.domain.events import ApplicationEvent
from app.infra.messaging.codec import MalformedMessageError, decode_event
from app.infra.messaging.connection import RabbitConnection
from app.infra.messaging.topology import declare_events_exchange

logger = logging.getLogger(__name__)

type EventSink = Callable[[ApplicationEvent], Awaitable[None]]


class EventSubscriber:
    """Своя очередь у каждого процесса api: событие доходит до всех WebSocket-подключений,
    на каком бы экземпляре они ни висели.
    """

    def __init__(self, connection: RabbitConnection, deliver: EventSink) -> None:
        self._connection = connection
        self._deliver = deliver
        self._channel: AbstractChannel | None = None

    async def start(self) -> None:
        self._channel = await self._connection.channel()
        exchange = await declare_events_exchange(self._channel)
        queue = await self._channel.declare_queue(exclusive=True, auto_delete=True)
        await queue.bind(exchange)
        # Без подтверждений: потерянное событие клиент восстановит по snapshot при переподключении.
        await queue.consume(self._on_message, no_ack=True)

    async def stop(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        try:
            event = decode_event(message.body)
        except MalformedMessageError:
            logger.exception("Битое событие из applications.events пропущено")
            return
        try:
            await self._deliver(event)
        except Exception:
            logger.exception("Не удалось доставить событие по заявке %s", event.application_id)
