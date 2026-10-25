"""Топология RabbitMQ (BACKEND_SPEC, раздел 11).

Объявление идемпотентно, его делают и api, и worker.
"""

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue
from pamqp.common import FieldTable

PROCESS_QUEUE = "applications.process"
DLX = "applications.dlx"
DLQ = "applications.process.dlq"
EVENTS_EXCHANGE = "applications.events"
# После стольких доставок RabbitMQ сам уводит сообщение в DLQ, заявку потом подхватит reconciler.
DELIVERY_LIMIT = 5

_QUORUM: FieldTable = {"x-queue-type": "quorum"}


async def declare_process_queue(channel: AbstractChannel) -> AbstractQueue:
    dlx = await channel.declare_exchange(DLX, ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(DLQ, durable=True, arguments=_QUORUM)
    await dlq.bind(dlx)
    return await channel.declare_queue(
        PROCESS_QUEUE,
        durable=True,
        arguments={
            **_QUORUM,
            "x-delivery-limit": DELIVERY_LIMIT,
            "x-dead-letter-exchange": DLX,
        },
    )


async def declare_events_exchange(channel: AbstractChannel) -> AbstractExchange:
    return await channel.declare_exchange(EVENTS_EXCHANGE, ExchangeType.FANOUT, durable=True)


async def declare_topology(channel: AbstractChannel) -> None:
    await declare_process_queue(channel)
    await declare_events_exchange(channel)
