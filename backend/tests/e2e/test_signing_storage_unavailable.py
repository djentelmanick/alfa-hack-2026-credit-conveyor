"""Выключенный MinIO при подписании: выдача откатывается целиком (BACKEND_SPEC, раздел 14).

Приложение поднимается с адресом S3, на котором никто не слушает. До подписания сценарию
Колоса S3 не нужен, поэтому заявка доходит до SIGNING как обычно.
"""

import pytest

from app.infra.config.settings import Settings
from tests.e2e.conftest import E2E
from tests.e2e.flow import API, auth, create_application, login, post, wait_for_status
from tests.fakes.scenarios import KOLOS

# Порт 9 (discard) в тестовом окружении никто не слушает: соединение отклоняется сразу.
DEAD_S3 = "http://127.0.0.1:9"


@pytest.fixture
def e2e_settings(e2e_settings: Settings) -> Settings:
    return e2e_settings.model_copy(update={"s3_endpoint": DEAD_S3})


def test_signing_is_rolled_back_when_storage_is_down(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    wait_for_status(e2e.client, token, application_id, "APPROVED")
    post(e2e.client, token, "/applications/accept", {"application_id": application_id})
    code = post(e2e.client, token, "/applications/signing/code", {"application_id": application_id})

    def confirm() -> tuple[int, str]:
        response = e2e.client.post(
            f"{API}/applications/signing/confirm",
            json={"application_id": application_id, "code": code["code"]},
            headers=auth(token),
        )
        return response.status_code, response.json()["error"]["code"]

    assert confirm() == (503, "STORAGE_UNAVAILABLE")
    # Код не сгорел: клиент может повторить, когда хранилище вернётся.
    assert confirm() == (503, "STORAGE_UNAVAILABLE")

    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert card["status"] == "SIGNING"
    assert "contract_id" not in card
    assert post(e2e.client, token, "/contracts/list", {}) == []
    history = post(e2e.client, token, "/applications/history", {"application_id": application_id})
    assert history[-1]["to_status"] == "SIGNING"
