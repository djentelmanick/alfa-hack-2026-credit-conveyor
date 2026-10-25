import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.websockets import WebSocket

from app.domain.entities import Application, TokenClaims
from app.domain.enums import ApplicationStatus, Role
from app.domain.events import ApplicationEvent
from app.domain.ports import StatusPublisher, UnitOfWork
from app.domain.state_machine import FINAL_STATUSES
from app.infra.realtime.messages import event_message, snapshot_message

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset(ApplicationStatus) - FINAL_STATUSES


@dataclass(eq=False, slots=True)
class _Connection:
    websocket: WebSocket
    claims: TokenClaims
    # Snapshot и события шлют разные задачи: замок не даёт событию обогнать snapshot.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def sees(self, event: ApplicationEvent) -> bool:
        return self.claims.role is Role.ANALYST or event.applicant_id == self.claims.user_id


class WsConnectionManager(StatusPublisher):
    """Подключения хранятся в памяти одного процесса (ограничение MVP, BACKEND_SPEC раздел 11)."""

    def __init__(self, uow: Callable[[], UnitOfWork]) -> None:
        self._uow = uow
        self._connections: dict[WebSocket, _Connection] = {}

    async def connect(self, websocket: WebSocket, claims: TokenClaims) -> None:
        """Соединение уже принято. Регистрируем до snapshot, чтобы не потерять событие."""
        connection = _Connection(websocket, claims)
        async with connection.lock:
            self._connections[websocket] = connection
            await self.send_snapshot(websocket, claims)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def send_snapshot(self, websocket: WebSocket, claims: TokenClaims) -> None:
        await websocket.send_json(snapshot_message(await self._visible(claims)))

    async def send(self, websocket: WebSocket, message: dict[str, object]) -> None:
        connection = self._connections.get(websocket)
        if connection is None:
            return
        async with connection.lock:
            await websocket.send_json(message)

    async def publish(self, event: ApplicationEvent) -> None:
        message = event_message(event)
        for connection in list(self._connections.values()):
            if not connection.sees(event):
                continue
            try:
                async with connection.lock:
                    await connection.websocket.send_json(message)
            except Exception:
                # Клиент ушёл без close: конвейер из-за этого падать не должен.
                logger.info("Не удалось отправить событие по WebSocket, отключаем клиента")
                self.disconnect(connection.websocket)

    async def _visible(self, claims: TokenClaims) -> list[Application]:
        async with self._uow() as uow:
            if claims.role is Role.ANALYST:
                return await uow.applications.list_by_statuses(ACTIVE_STATUSES)
            items, _ = await uow.applications.list_for_applicant(claims.user_id)
            return items
