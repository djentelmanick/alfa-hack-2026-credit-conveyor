"""SCOPE раздел 12, сценарий 2: ООО «Ромашка» ликвидирована, отказ по стоп-фактору без скоринга."""

from tests.e2e.conftest import E2E
from tests.e2e.flow import (
    connect,
    create_application,
    login,
    post,
    read_until_status,
    statuses,
    wait_for_status,
)
from tests.fakes.scenarios import ROMASHKA
from tests.fakes.scoring import FixedScoringEngine


def test_romashka_is_rejected_by_stop_factor(e2e: E2E, scoring: FixedScoringEngine) -> None:
    token = login(e2e.client, "romashka")
    application_id = create_application(
        e2e.client, token, ROMASHKA, amount="1000000.00", term_months=12
    )

    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "REJECTED")
        messages = read_until_status(ws, application_id, "REJECTED")

    assert statuses(messages) == ["SUBMITTED", "CHECKING", "REJECTED"]
    rejected = messages[-1]
    assert rejected["from_status"] == "CHECKING"
    assert rejected["reason"] == "COMPANY_INACTIVE"
    assert rejected["is_final"] is True
    assert scoring.calls == []

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    assert decision["outcome"] == "REJECTED"
    assert decision["reasons"] == [
        "Компания не действует: ликвидация, банкротство или реорганизация"
    ]
    assert "scoring" not in decision

    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert card["allowed_actions"] == []
