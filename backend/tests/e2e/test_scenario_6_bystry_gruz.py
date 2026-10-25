"""Сценарий 6: ООО «Быстрый груз», 1,5 млн на 24 мес без залога.

Закрытая просрочка 45 дней за год, нагрузка 55% и грузоперевозки дают −20 баллов, класс D:
отказ без ручной проверки, а клиент видит, какие факторы снизили балл.
"""

import pytest

from app.domain.scoring import ScoringEngine, ScoringModel
from app.infra.config.catalog import load_catalog
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
from tests.fakes.scenarios import BYSTRY_GRUZ

ANTIFRAUD_CODES = {"MASS_ADDRESS", "MASS_DIRECTOR", "CREDIT_INQUIRIES"}


@pytest.fixture
def scoring() -> ScoringModel:
    # Тексты причин отказа даёт настоящая модель, у FixedScoringEngine их нет.
    return ScoringEngine(load_catalog())


def test_bystry_gruz_is_rejected_with_negative_factors(e2e: E2E) -> None:
    token = login(e2e.client, "gruz")
    application_id = create_application(
        e2e.client, token, BYSTRY_GRUZ, amount="1500000.00", term_months=24
    )

    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "REJECTED")
        messages = read_until_status(ws, application_id, "REJECTED")

    assert statuses(messages) == ["SUBMITTED", "CHECKING", "SCORING", "REJECTED"]
    assert messages[-1]["is_final"] is True

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    assert decision["outcome"] == "REJECTED"
    assert "rate" not in decision or decision["rate"] is None
    assert (decision["scoring"]["total"], decision["scoring"]["risk_class"]) == (-20, "D")
    # Худшие факторы первыми, тексты берутся из модели скоринга.
    assert decision["reasons"] == [
        "Были серьёзные просрочки за последний год",
        "Высокая долговая нагрузка",
        "Отрасль с высоким уровнем риска",
    ]
    assert {f["code"] for f in decision["scoring"]["factors"]}.isdisjoint(ANTIFRAUD_CODES)
    assert [(f["label"], f["points"]) for f in decision["key_factors"]] == [
        ("Кредитная история", -20),
        ("Долговая нагрузка", -15),
        ("Сумма заявки / годовая выручка", 15),
    ]

    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert card["allowed_actions"] == []
