"""Аналитик и упавший источник: одобрить нельзя, повторная проверка и контрпредложение с залогом."""

from datetime import timedelta

from tests.e2e.conftest import ANALYST, E2E, audit_actions
from tests.e2e.flow import (
    API,
    auth,
    connect,
    create_application,
    login,
    post,
    read_until_status,
    statuses,
    wait_for_status,
)
from tests.fakes.scenarios import KOLOS

COMMENT = "БКИ не ответило, повторяем запрос"


def _kolos_in_manual_review_without_bki(e2e: E2E) -> tuple[str, str, str]:
    analyst = login(e2e.client, ANALYST)
    failure = {"source": "BKI", "inn": KOLOS, "enabled": True}
    post(e2e.client, analyst, "/dev/mocks/failures", failure)
    token = login(e2e.client, "kolos")
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    wait_for_status(e2e.client, token, application_id, "MANUAL_REVIEW")
    post(e2e.client, analyst, "/dev/mocks/failures", failure | {"enabled": False})
    return analyst, token, application_id


def test_approve_is_blocked_until_recheck_gets_all_sources(e2e: E2E) -> None:
    analyst, token, application_id = _kolos_in_manual_review_without_bki(e2e)

    (item,) = post(e2e.client, analyst, "/analyst/applications/list", {"queue": "manual"})["items"]
    assert item["manual_reasons"] == ["SOURCE_UNAVAILABLE"]
    report = post(
        e2e.client, analyst, "/analyst/applications/report", {"application_id": application_id}
    )
    bki = [s for s in report["sources"] if s["source"] == "BKI"]
    assert bki[0]["status"] == "FAILED"
    assert bki[0]["attempts"] == e2e.settings.source_retry_attempts
    assert report["decision"]["manual_reasons"] == ["SOURCE_UNAVAILABLE"]

    blocked = e2e.client.post(
        f"{API}/analyst/applications/approve",
        json={"application_id": application_id, "rate": "23.5", "comment": "Одобряю"},
        headers=auth(analyst),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOURCES_NOT_READY"

    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        e2e.clock.advance(timedelta(minutes=1))
        response = e2e.client.post(
            f"{API}/analyst/applications/recheck",
            json={"application_id": application_id, "comment": COMMENT},
            headers=auth(analyst),
        )
        assert response.status_code == 202, response.text
        assert response.json() == {"application_id": application_id, "status": "CHECKING"}
        # Источники ответили, класс A без поводов для ручной проверки: система одобряет сама.
        wait_for_status(e2e.client, token, application_id, "APPROVED")
        messages = read_until_status(ws, application_id, "APPROVED")

    assert statuses(messages) == ["CHECKING", "SCORING", "APPROVED"]
    assert messages[0]["from_status"] == "MANUAL_REVIEW"
    assert audit_actions(e2e, application_id) == [("RECHECK", {"comment": COMMENT})]


def test_secured_counter_offer_sends_client_back_for_collateral(e2e: E2E) -> None:
    analyst, token, application_id = _kolos_in_manual_review_without_bki(e2e)
    option = {
        "product_code": "LUMP_SUM",
        "variant": "SECURED",
        "amount": "2000000.00",
        "term_months": 24,
        "rate": "21.5",
    }

    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        offered = post(
            e2e.client,
            analyst,
            "/analyst/applications/counter-offer",
            {"application_id": application_id, "options": [option], "comment": "Только с залогом"},
        )
        assert offered["status"] == "COUNTER_OFFER"
        (event,) = read_until_status(ws, application_id, "COUNTER_OFFER")
        assert event["expires_at"] is not None

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    (offer,) = decision["offers"]
    assert (offer["variant"], offer["rate"]) == ("SECURED", "21.5")

    card = post(
        e2e.client,
        token,
        "/applications/accept",
        {"application_id": application_id, "offer_id": offer["id"]},
    )
    assert card["status"] == "NEEDS_INFO"
    assert [i["code"] for i in card["info_request"]["items"]] == ["COLLATERAL"]

    post(
        e2e.client,
        token,
        "/applications/update",
        {
            "application_id": application_id,
            "collateral": {
                "type": "EQUIPMENT",
                "description": "Печи и тестомесы",
                "estimated_value": "1500000.00",
            },
        },
    )
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    # После залога заявка снова проходит конвейер и по поводу «залог» идёт к аналитику.
    wait_for_status(e2e.client, token, application_id, "MANUAL_REVIEW")
    (item,) = post(e2e.client, analyst, "/analyst/applications/list", {"queue": "manual"})["items"]
    assert item["manual_reasons"] == ["SECURED_VARIANT"]
    assert [a for a, _ in audit_actions(e2e, application_id)] == ["COUNTER_OFFER"]
