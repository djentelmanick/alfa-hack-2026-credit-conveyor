import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.v1.deps import get_container
from app.domain.entities import TokenClaims
from app.domain.errors import TokenExpired, TokenInvalid
from app.domain.ports import TokenService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

SUBPROTOCOL = "bearer"
CLOSE_UNAUTHORIZED = 4401


def _token_from_subprotocols(subprotocols: list[str]) -> str | None:
    # Браузер не умеет ставить Authorization у WebSocket, поэтому токен идёт вторым подпротоколом.
    if len(subprotocols) != 2 or subprotocols[0].lower() != SUBPROTOCOL:
        return None
    return subprotocols[1].strip() or None


def _authenticate(websocket: WebSocket, tokens: TokenService) -> TokenClaims | None:
    token = _token_from_subprotocols(websocket.scope.get("subprotocols", []))
    if token is None:
        return None
    try:
        return tokens.decode(token)
    except TokenInvalid, TokenExpired:
        return None


@router.websocket("/ws/applications")
async def applications_ws(websocket: WebSocket) -> None:
    container = get_container(websocket)
    claims = _authenticate(websocket, container.tokens)
    offered = websocket.scope.get("subprotocols", [])
    if claims is None:
        # Закрытие до accept Starlette превращает в HTTP 403, и клиент не увидел бы код 4401.
        await websocket.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in offered else None)
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    hub = container.realtime
    await hub.connect(websocket, claims)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("type") == "ping":
                await hub.send(websocket, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)
