"""С настоящей заглушкой ScoringEngine заявка уходит аналитику (BACKEND_SPEC, раздел 7)."""

import pytest

from app.domain.scoring import ScoringEngine, ScoringModel
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
from tests.fakes.scenarios import KOLOS


@pytest.fixture
def scoring() -> ScoringModel:
    return ScoringEngine()


def test_scoring_stub_sends_application_to_manual_review(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "MANUAL_REVIEW")
        messages = read_until_status(ws, application_id, "MANUAL_REVIEW")

    assert statuses(messages) == ["SUBMITTED", "CHECKING", "SCORING", "MANUAL_REVIEW"]
    assert messages[-1]["reason"] == "SCORING_NOT_IMPLEMENTED"

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    assert decision["outcome"] == "MANUAL_REVIEW"
    assert decision["reasons"] == ["Заявку рассмотрит специалист банка"]
    assert "scoring" not in decision
    assert "rate" not in decision
