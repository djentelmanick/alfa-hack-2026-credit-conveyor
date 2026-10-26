"""SCOPE раздел 12, сценарий 3: ИП Соколов, 4,5 млн на 24 мес без залога.

Класс B (55 баллов), сумма выше лимита от выручки без залога: контрпредложение по 26,5%,
3,6 млн без залога или 4,5 млн с залогом. Клиент выбирает 3,6 млн, подписывает и получает кредит.
"""

from decimal import Decimal

from tests.e2e.conftest import ANALYST, E2E, contract_files
from tests.e2e.flow import (
    IE_CONSENTS,
    connect,
    create_application,
    login,
    post,
    read_until_status,
    sign,
    statuses,
    wait_for_status,
)
from tests.fakes.scenarios import SOKOLOV


def test_sokolov_takes_3_6m_without_collateral_and_signs(e2e: E2E) -> None:
    token = login(e2e.client, "sokolov")
    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        application_id = create_application(
            e2e.client,
            token,
            SOKOLOV,
            consents=IE_CONSENTS,
            amount="4500000.00",
            term_months=24,
            owner_guarantee_consent=False,
        )
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "COUNTER_OFFER")

        decision = post(
            e2e.client, token, "/applications/decision", {"application_id": application_id}
        )
        assert (decision["outcome"], decision["rate"]) == ("COUNTER_OFFER", "26.5")
        assert "scoring" not in decision
        report = post(
            e2e.client,
            login(e2e.client, ANALYST),
            "/analyst/applications/report",
            {"application_id": application_id},
        )
        assert (report["scoring"]["total"], report["scoring"]["risk_class"]) == (55, "B")
        unsecured, secured = decision["offers"]
        assert (unsecured["variant"], unsecured["amount"], unsecured["rate"]) == (
            "UNSECURED",
            "3600000.00",
            "26.5",
        )
        assert (secured["variant"], secured["amount"]) == ("SECURED", "4500000.00")

        accepted = post(
            e2e.client,
            token,
            "/applications/accept",
            {"application_id": application_id, "offer_id": unsecured["id"]},
        )
        assert accepted["status"] == "SIGNING"

        contract_id = sign(e2e.client, token, application_id)
        messages = read_until_status(ws, application_id, "ISSUED")

    assert statuses(messages)[-3:] == ["COUNTER_OFFER", "SIGNING", "ISSUED"]

    contract = post(e2e.client, token, "/contracts/get", {"contract_id": contract_id})
    assert (contract["product_code"], contract["variant"]) == ("LUMP_SUM", "UNSECURED")
    assert (contract["principal"], contract["outstanding"]) == ("3600000.00", "3600000.00")
    assert (contract["rate"], contract["term_months"]) == ("26.5", 24)

    schedule = post(e2e.client, token, "/contracts/schedule", {"contract_id": contract_id})
    assert len(schedule) == 24
    assert sum(Decimal(item["principal"]) for item in schedule) == Decimal("3600000.00")

    operations = post(e2e.client, token, "/contracts/operations", {"contract_id": contract_id})
    assert [(op["type"], op["amount"]) for op in operations["items"]] == [
        ("DISBURSEMENT", "3600000.00")
    ]
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf"]
