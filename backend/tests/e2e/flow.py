"""Шаги клиента в e2e: вход, согласия, заявка, ожидание конвейера, чтение WebSocket."""

import time
from typing import Any

import httpx
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.fakes.auth import PASSWORD
from tests.fakes.scenarios import KOLOS

API = "/api/v1"
WS_URL = f"{API}/ws/applications"
PIPELINE_TIMEOUT_SEC = 15.0
LLC_CONSENTS = ["BKI_COMPANY", "BKI_PERSONAL", "PERSONAL_DATA"]
IE_CONSENTS = ["BKI_PERSONAL", "PERSONAL_DATA"]


def login(client: TestClient, user: str) -> str:
    response = client.post(f"{API}/auth/login", json={"login": user, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def post(client: TestClient, token: str, path: str, body: dict[str, Any]) -> Any:
    response = client.post(f"{API}{path}", json=body, headers=auth(token))
    assert response.status_code < 300, f"{path}: {response.status_code} {response.text}"
    return response.json() if response.content else None


def claim_authority(
    client: TestClient, token: str, inn: str, role: str, poa: dict[str, Any]
) -> httpx.Response:
    """Шаг анкеты «Кем вы приходитесь компании»: проверка доверенности и привязка компании."""
    return client.post(
        f"{API}/companies/authority",
        json={"inn": inn, "role": role, "power_of_attorney": poa},
        headers=auth(token),
    )


def create_application(
    client: TestClient, token: str, inn: str, *, consents: list[str] = LLC_CONSENTS, **form: Any
) -> str:
    post(client, token, "/consents/give", {"inn": inn, "types": consents})
    body = {
        "inn": inn,
        "purpose": "DEVELOPMENT",
        "product_code": "LUMP_SUM",
        "variant": "UNSECURED",
        "owner_guarantee_consent": True,
    } | form
    application_id: str = post(client, token, "/applications/create", body)["id"]
    return application_id


def upload_statement(
    client: TestClient, token: str, application_id: str, name: str, data: bytes
) -> Any:
    response = client.post(
        f"{API}/applications/statements/upload",
        data={"application_id": application_id},
        files={"file": (name, data, "text/plain")},
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_status(client: TestClient, token: str, application_id: str, status: str) -> Any:
    """Конвейер идёт в фоне: ждём статус с дедлайном, чтобы упавшая задача не повесила тест."""
    deadline = time.monotonic() + PIPELINE_TIMEOUT_SEC
    while True:
        card = post(client, token, "/applications/get", {"application_id": application_id})
        if card["status"] == status:
            return card
        assert time.monotonic() < deadline, f"Заявка застряла в {card['status']}, ждали {status}"
        time.sleep(0.05)


def read_until_status(ws: WebSocketTestSession, application_id: str, status: str) -> list[Any]:
    """Сообщения по заявке до смены статуса на status включительно.

    Вызывается после wait_for_status, поэтому нужные сообщения уже отправлены или вот-вот будут.
    """
    messages = []
    while True:
        message = ws.receive_json()
        if message.get("application_id") != application_id:
            continue
        messages.append(message)
        if message["type"] == "application.status_changed" and message["to_status"] == status:
            return messages


def statuses(messages: list[Any]) -> list[str]:
    return [m["to_status"] for m in messages if m["type"] == "application.status_changed"]


def steps(messages: list[Any]) -> list[str]:
    return [m["step"] for m in messages if m["type"] == "application.step"]


def connect(client: TestClient, token: str) -> WebSocketTestSession:
    ws = client.websocket_connect(WS_URL, subprotocols=["bearer", token])
    return ws


def sign(client: TestClient, token: str, application_id: str) -> str:
    """Код подписания и подтверждение им: заявка из SIGNING уходит в ISSUED."""
    code = post(client, token, "/applications/signing/code", {"application_id": application_id})
    confirmed = post(
        client,
        token,
        "/applications/signing/confirm",
        {"application_id": application_id, "code": code["code"]},
    )
    assert confirmed["status"] == "ISSUED"
    contract_id: str = confirmed["contract_id"]
    return contract_id


def download(url: str) -> bytes:
    """Presigned-ссылка ведёт в MinIO напрямую, мимо приложения, как из браузера."""
    response = httpx.get(url, timeout=10)
    assert response.status_code == 200, response.text
    return response.content


def issue_kolos_loan(client: TestClient, token: str) -> str:
    """Сценарий 1 до выдачи: 2 млн на 12 мес, одобрено, подписано. Договор от 2026-10-24."""
    application_id = create_application(client, token, KOLOS, amount="2000000.00", term_months=12)
    post(client, token, "/applications/submit", {"application_id": application_id})
    wait_for_status(client, token, application_id, "APPROVED")
    post(client, token, "/applications/accept", {"application_id": application_id})
    return sign(client, token, application_id)


def error_code(response: httpx.Response) -> tuple[int, str]:
    body = response.json()["error"]
    details = body.get("details") or []
    return response.status_code, details[0]["code"] if details else body["code"]
