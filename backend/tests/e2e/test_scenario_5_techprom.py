"""SCOPE раздел 12, сценарий 5: ООО «Техпром», ВКЛ с залогом, лимит 12 млн на 60 мес.

Класс A (100 баллов), но залог и сумма больше 5 млн дают ручную проверку. Аналитик видит
заявку в очереди, читает отчёт и одобряет по 23% с комментарием. Клиент подписывает,
и открывается кредитная линия с лимитом 12 млн.
"""

from datetime import timedelta
from typing import Any

from tests.e2e.conftest import ANALYST, E2E, audit_actions, contract_files
from tests.e2e.flow import (
    API,
    auth,
    connect,
    create_application,
    error_code,
    login,
    post,
    read_until_status,
    sign,
    statuses,
    wait_for_status,
)
from tests.fakes.scenarios import TECHPROM

# FixedClock стоит на 2026-10-24 09:00 UTC, аналитик решает через 10 минут, одобрение живёт 7 дней.
APPROVED_EXPIRES_AT = "2026-10-31T09:10:00Z"
COMMENT = "Помещение в собственности, оценка 9 млн покрывает лимит с запасом"
REPORT_SECTIONS = {
    "application",
    "provided",
    "sources",
    "comparisons",
    "turnover",
    "top_payers",
    "scoring",
    "decision",
    "history",
}


def _submit_techprom(e2e: E2E, token: str) -> str:
    application_id = create_application(
        e2e.client,
        token,
        TECHPROM,
        purpose="REGULAR_PURCHASES",
        product_code="REVOLVING_LINE",
        variant="SECURED",
        amount="12000000.00",
        term_months=60,
        owner_guarantee_consent=False,
        collateral={
            "type": "REAL_ESTATE",
            "description": "Производственное помещение",
            "estimated_value": "9000000.00",
        },
    )
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    return application_id


def test_techprom_goes_to_analyst_and_is_approved_at_23(e2e: E2E) -> None:
    client_token = login(e2e.client, "techprom")
    analyst = login(e2e.client, ANALYST)

    with connect(e2e.client, client_token) as client_ws, connect(e2e.client, analyst) as analyst_ws:
        assert client_ws.receive_json()["type"] == "snapshot"
        assert analyst_ws.receive_json()["type"] == "snapshot"

        application_id = _submit_techprom(e2e, client_token)
        wait_for_status(e2e.client, client_token, application_id, "MANUAL_REVIEW")
        to_review = read_until_status(client_ws, application_id, "MANUAL_REVIEW")
        assert statuses(to_review) == ["SUBMITTED", "CHECKING", "SCORING", "MANUAL_REVIEW"]
        assert to_review[-1]["reason"] == "SECURED_VARIANT, AMOUNT_OVER_5M"
        # Аналитик видит события всех заявок, в том числе эту.
        assert statuses(read_until_status(analyst_ws, application_id, "MANUAL_REVIEW"))[-1] == (
            "MANUAL_REVIEW"
        )

        queue = post(e2e.client, analyst, "/analyst/applications/list", {"queue": "manual"})
        (item,) = queue["items"]
        assert item["id"] == application_id
        assert item["company_name"] == 'ООО "ТЕХПРОМ"'
        assert item["manual_reasons"] == ["SECURED_VARIANT", "AMOUNT_OVER_5M"]
        assert (item["risk_class"], item["amount"]) == ("A", "12000000.00")

        report = post(
            e2e.client, analyst, "/analyst/applications/report", {"application_id": application_id}
        )
        _assert_report_before_decision(report)

        no_comment = e2e.client.post(
            f"{API}/analyst/applications/approve",
            json={"application_id": application_id, "rate": "23", "comment": "  "},
            headers=auth(analyst),
        )
        assert no_comment.status_code == 422, no_comment.text
        assert no_comment.json()["error"]["details"][0]["field"] == "comment"

        e2e.clock.advance(timedelta(minutes=10))
        approved = post(
            e2e.client,
            analyst,
            "/analyst/applications/approve",
            {"application_id": application_id, "rate": "23", "comment": COMMENT},
        )
        assert approved["status"] == "APPROVED"
        assert approved["expires_at"] == APPROVED_EXPIRES_AT

        (event,) = read_until_status(client_ws, application_id, "APPROVED")
        assert (event["from_status"], event["to_status"]) == ("MANUAL_REVIEW", "APPROVED")
        assert event["expires_at"] == APPROVED_EXPIRES_AT
        assert event["is_final"] is False
        assert COMMENT not in event["reason"]

    decision = post(
        e2e.client, client_token, "/applications/decision", {"application_id": application_id}
    )
    assert (decision["outcome"], decision["rate"]) == ("APPROVED", "23.0")
    card = post(e2e.client, client_token, "/applications/get", {"application_id": application_id})
    assert card["allowed_actions"] == ["ACCEPT", "CANCEL"]

    after = post(
        e2e.client, analyst, "/analyst/applications/report", {"application_id": application_id}
    )
    analyst_part = after["decision"]["analyst"]
    assert analyst_part["name"] == "Орлова Мария Игоревна"
    assert (analyst_part["rate"], analyst_part["comment"]) == ("23.0", COMMENT)
    assert after["decision"]["rule_code"] == "MANUAL_REVIEW"
    last = after["history"][-1]
    assert (last["to_status"], last["actor"]["role"]) == ("APPROVED", "ANALYST")

    assert audit_actions(e2e, application_id) == [("APPROVE", {"comment": COMMENT, "rate": "23"})]

    repeated = e2e.client.post(
        f"{API}/analyst/applications/approve",
        json={"application_id": application_id, "rate": "23", "comment": COMMENT},
        headers=auth(analyst),
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "INVALID_STATUS"


def _assert_report_before_decision(report: dict[str, object]) -> None:
    assert set(report) == REPORT_SECTIONS

    provided = report["provided"]
    assert isinstance(provided, dict)
    assert provided["applicant"] == {
        "full_name": "Кузнецов Игорь Валентинович",
        "passport_verified": True,
    }
    assert provided["collateral"]["estimated_value"] == "9000000.00"
    assert {c["type"] for c in provided["consents"]} == {
        "BKI_COMPANY",
        "BKI_PERSONAL",
        "PERSONAL_DATA",
    }

    sources = report["sources"]
    assert isinstance(sources, list)
    assert {s["status"] for s in sources} == {"OK"}
    egrul = next(s for s in sources if s["source"] == "EGRUL")
    assert egrul["data"]["director"] == "КУЗНЕЦОВ ИГОРЬ ВАЛЕНТИНОВИЧ"

    comparisons = report["comparisons"]
    assert isinstance(comparisons, list)
    by_code = {c["code"]: c for c in comparisons}
    assert by_code["COLLATERAL_COVERAGE"]["stage"] == "FORM"
    assert by_code["COLLATERAL_COVERAGE"]["outcome"] == "PASSED"
    assert by_code["COMPANY_INACTIVE"]["stage"] == "CHECKING"
    assert all(c["title"] for c in comparisons)

    turnover = report["turnover"]
    assert isinstance(turnover, list)
    assert len(turnover) == 12
    assert turnover[0]["incoming"] == "5100000.00"
    assert turnover[0]["outgoing"] == "4488000.00"
    top_payers = report["top_payers"]
    assert isinstance(top_payers, list)
    assert top_payers[0]["name"] == 'АО "МАШЗАВОД"'

    scoring = report["scoring"]
    assert isinstance(scoring, dict)
    assert (scoring["total"], scoring["risk_class"], scoring["debt_load_pct"]) == (100, "A", 15)

    decision = report["decision"]
    assert isinstance(decision, dict)
    assert decision["rule_code"] == "MANUAL_REVIEW"
    assert decision["outcome"] == "MANUAL_REVIEW"
    assert decision["manual_reasons"] == ["SECURED_VARIANT", "AMOUNT_OVER_5M"]
    # Подсказка аналитику: ставка по классу A, нижняя граница ВКЛ с залогом.
    assert decision["system_rate"] == "23.0"
    assert "analyst" not in decision


def test_techprom_signs_and_credit_line_is_opened(e2e: E2E) -> None:
    client_token = login(e2e.client, "techprom")
    analyst = login(e2e.client, ANALYST)
    application_id = _submit_techprom(e2e, client_token)
    wait_for_status(e2e.client, client_token, application_id, "MANUAL_REVIEW")
    post(
        e2e.client,
        analyst,
        "/analyst/applications/approve",
        {"application_id": application_id, "rate": "23", "comment": COMMENT},
    )
    post(e2e.client, client_token, "/applications/accept", {"application_id": application_id})

    with connect(e2e.client, client_token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        contract_id = sign(e2e.client, client_token, application_id)
        (issued,) = read_until_status(ws, application_id, "ISSUED")
    assert issued["from_status"] == "SIGNING"

    contract = post(e2e.client, client_token, "/contracts/get", {"contract_id": contract_id})
    assert (contract["product_code"], contract["variant"]) == ("REVOLVING_LINE", "SECURED")
    assert (contract["rate"], contract["term_months"]) == ("23.0", 60)
    assert (contract["limit"], contract["available"]) == ("12000000.00", "12000000.00")
    assert (contract["outstanding"], contract["tranches"]) == ("0.00", [])
    # С залогом транши берутся до момента за 6 месяцев до конца линии (SCOPE, раздел 2).
    assert contract["draw_until"] == "2031-04-24"
    assert contract["allowed_operations"] == ["DRAW_TRANCHE", "FULL_REPAYMENT"]
    assert "principal" not in contract
    assert "next_payment" not in contract

    assert post(e2e.client, client_token, "/contracts/schedule", {"contract_id": contract_id}) == []
    operations = post(
        e2e.client, client_token, "/contracts/operations", {"contract_id": contract_id}
    )
    assert [(op["type"], op["amount"], op["balance_after"]) for op in operations["items"]] == [
        ("LIMIT_OPENED", "12000000.00", "0.00")
    ]
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf"]

    # Аналитик открывает тот же договор из отчёта по заявке.
    report = post(
        e2e.client, analyst, "/analyst/applications/report", {"application_id": application_id}
    )
    assert report["contract"]["id"] == contract_id
    assert report["contract"]["versions_count"] == 1
    assert post(e2e.client, analyst, "/contracts/get", {"contract_id": contract_id})["id"] == (
        contract_id
    )


def test_techprom_two_tranches_and_limit_restored(e2e: E2E) -> None:
    """SCOPE раздел 12, после выдачи: два транша, погашение одного восстанавливает лимит."""
    client_token = login(e2e.client, "techprom")
    analyst = login(e2e.client, ANALYST)
    application_id = _submit_techprom(e2e, client_token)
    wait_for_status(e2e.client, client_token, application_id, "MANUAL_REVIEW")
    post(
        e2e.client,
        analyst,
        "/analyst/applications/approve",
        {"application_id": application_id, "rate": "23", "comment": COMMENT},
    )
    post(e2e.client, client_token, "/applications/accept", {"application_id": application_id})
    contract_id = sign(e2e.client, client_token, application_id)

    def draw(amount: str) -> Any:
        return e2e.client.post(
            f"{API}/contracts/tranches/draw",
            json={"contract_id": contract_id, "amount": amount},
            headers=auth(client_token),
        )

    first = draw("1000000.00")
    assert first.status_code == 201
    assert first.json()["due_at"] == "2027-04-24"
    assert draw("2000000.00").status_code == 201
    contract = post(e2e.client, client_token, "/contracts/get", {"contract_id": contract_id})
    assert (contract["available"], contract["outstanding"]) == ("9000000.00", "3000000.00")
    assert len(contract["tranches"]) == 2

    restored = post(
        e2e.client,
        client_token,
        "/contracts/tranches/repay",
        {"contract_id": contract_id, "tranche_id": first.json()["id"], "amount": "1000000.00"},
    )
    assert (restored["available"], restored["outstanding"]) == ("10000000.00", "2000000.00")
    assert error_code(draw("49999.99")) == (422, "TRANCHE_BELOW_MIN")

    # Закрытый договор отвечает 409 при любой сумме, а не ошибкой проверки суммы.
    post(e2e.client, client_token, "/contracts/repay", {"contract_id": contract_id, "kind": "FULL"})
    assert error_code(draw("49999.99")) == (409, "INVALID_STATUS")
