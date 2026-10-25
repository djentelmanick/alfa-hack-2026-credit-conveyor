"""Источник не ответил после всех попыток: ручная проверка без скоринга (SCOPE, раздел 4)."""

from tests.e2e.conftest import ANALYST, E2E
from tests.e2e.flow import (
    connect,
    create_application,
    login,
    post,
    read_until_status,
    statuses,
    wait_for_status,
)
from tests.fakes.scenarios import KOLOS
from tests.fakes.scoring import FixedScoringEngine


def test_broken_bki_sends_application_to_manual_review(
    e2e: E2E, scoring: FixedScoringEngine
) -> None:
    analyst = login(e2e.client, ANALYST)
    switched = post(
        e2e.client, analyst, "/dev/mocks/failures", {"source": "BKI", "inn": KOLOS, "enabled": True}
    )
    assert switched == {"source": "BKI", "inn": KOLOS, "enabled": True}

    token = login(e2e.client, "kolos")
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "MANUAL_REVIEW")
        messages = read_until_status(ws, application_id, "MANUAL_REVIEW")

    assert statuses(messages) == ["SUBMITTED", "CHECKING", "MANUAL_REVIEW"]
    assert messages[-1]["reason"] == "SOURCE_UNAVAILABLE"
    assert scoring.calls == []

    history = post(e2e.client, token, "/applications/history", {"application_id": application_id})
    assert [h["to_status"] for h in history] == ["SUBMITTED", "CHECKING", "MANUAL_REVIEW"]
    assert history[-1]["actor"] == {"id": None, "role": "SYSTEM", "name": "Система"}
