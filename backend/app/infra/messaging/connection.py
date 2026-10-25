import asyncio

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection


class RabbitConnection:
    """Одно robust-соединение на процесс: после разрыва aio-pika сам восстанавливает
    каналы, очереди и подписки.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._publish_channel: AbstractChannel | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        self._connection = None
        self._publish_channel = None

    async def channel(self, *, prefetch: int | None = None) -> AbstractChannel:
        channel = await self._ensure_connection().channel()
        if prefetch is not None:
            await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def publish_channel(self) -> AbstractChannel:
        """Общий канал для публикации задач и событий, с подтверждениями брокера."""
        async with self._lock:
            if self._publish_channel is None or self._publish_channel.is_closed:
                self._publish_channel = await self.channel()
            return self._publish_channel

    def _ensure_connection(self) -> AbstractRobustConnection:
        if self._connection is None:
            raise RuntimeError("RabbitConnection не запущено: вызовите start()")
        return self._connection
