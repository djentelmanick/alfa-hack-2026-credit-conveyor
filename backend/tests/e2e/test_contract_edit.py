"""Правка договора аналитиком (SCOPE, раздел 8): новая версия, новый PDF, старая версия остаётся."""

from decimal import Decimal
from typing import Any

from tests.e2e.conftest import ANALYST, E2E, audit_actions, contract_files
from tests.e2e.flow import (
    API,
    auth,
    create_application,
    download,
    login,
    post,
    sign,
    wait_for_status,
)
from tests.fakes.scenarios import KOLOS

COMMENT = "Снижение ставки по итогам пересмотра риска"


def _issued_kolos(e2e: E2E, token: str) -> str:
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    wait_for_status(e2e.client, token, application_id, "APPROVED")
    post(e2e.client, token, "/applications/accept", {"application_id": application_id})
    return sign(e2e.client, token, application_id)


def _update(e2e: E2E, token: str, body: dict[str, Any]) -> Any:
    return e2e.client.post(f"{API}/analyst/contracts/update", json=body, headers=auth(token))


def test_rate_change_creates_version_2_with_new_pdf(e2e: E2E) -> None:
    client = login(e2e.client, "kolos")
    analyst = login(e2e.client, ANALYST)
    contract_id = _issued_kolos(e2e, client)
    first_schedule = post(e2e.client, client, "/contracts/schedule", {"contract_id": contract_id})

    updated = post(
        e2e.client,
        analyst,
        "/analyst/contracts/update",
        {"contract_id": contract_id, "rate": "25", "comment": COMMENT},
    )
    assert (updated["version_no"], updated["rate"], updated["term_months"]) == (2, "25.0", 12)
    assert updated["document"]["version_no"] == 2
    assert updated["principal"] == "2000000.00"

    v1, v2 = post(e2e.client, client, "/contracts/versions", {"contract_id": contract_id})
    assert v1["version_no"] == 1
    assert v2["changes"] == [{"field": "rate", "old": "23.5", "new": "25.0"}]
    assert (v2["author"]["role"], v2["author"]["name"]) == ("ANALYST", "Орлова Мария Игоревна")
    assert v2["reason"] == "Условия изменены аналитиком"
    # Комментарий аналитика внутренний: клиент видит только причину версии.
    assert COMMENT not in str(v2)
    assert v2["has_document"] is True

    second_schedule = post(e2e.client, client, "/contracts/schedule", {"contract_id": contract_id})
    old_schedule = post(
        e2e.client, client, "/contracts/schedule", {"contract_id": contract_id, "version_no": 1}
    )
    assert old_schedule == first_schedule
    assert [i["due_date"] for i in second_schedule] == [i["due_date"] for i in first_schedule]
    assert Decimal(second_schedule[0]["interest"]) > Decimal(first_schedule[0]["interest"])
    assert second_schedule[-1]["balance"] == "0.00"

    latest = post(e2e.client, client, "/contracts/document-url", {"contract_id": contract_id})
    assert latest["version_no"] == 2
    assert latest["file_name"].endswith("_v2.pdf")
    old = post(
        e2e.client, client, "/contracts/document-url", {"contract_id": contract_id, "version_no": 1}
    )
    assert old["version_no"] == 1
    assert download(latest["url"]).startswith(b"%PDF")
    assert download(old["url"]).startswith(b"%PDF")
    assert contract_files(e2e, contract_id) == [
        f"{contract_id}/v1.pdf",
        f"{contract_id}/v2.pdf",
    ]

    operations = post(e2e.client, client, "/contracts/operations", {"contract_id": contract_id})
    assert [op["type"] for op in operations["items"]] == ["DISBURSEMENT", "CONTRACT_EDITED"]
    assert operations["items"][1]["actor"] not in {"SYSTEM", None}

    assert audit_actions(e2e, contract_id) == [
        ("EDIT_CONTRACT", {"comment": COMMENT, "version_no": "2", "rate": "25"})
    ]

    missing = e2e.client.post(
        f"{API}/contracts/document-url",
        json={"contract_id": contract_id, "version_no": 3},
        headers=auth(client),
    )
    assert missing.status_code == 404


def test_term_change_rebuilds_schedule(e2e: E2E) -> None:
    client = login(e2e.client, "kolos")
    analyst = login(e2e.client, ANALYST)
    contract_id = _issued_kolos(e2e, client)

    updated = post(
        e2e.client,
        analyst,
        "/analyst/contracts/update",
        {"contract_id": contract_id, "term_months": 24, "comment": COMMENT},
    )
    assert (updated["version_no"], updated["term_months"], updated["rate"]) == (2, 24, "23.5")
    schedule = post(e2e.client, client, "/contracts/schedule", {"contract_id": contract_id})
    assert len(schedule) == 24
    assert schedule[-1]["due_date"] == "2028-10-24"


def test_contract_update_rejects_bad_requests(e2e: E2E) -> None:
    client = login(e2e.client, "kolos")
    analyst = login(e2e.client, ANALYST)
    contract_id = _issued_kolos(e2e, client)

    def error(token: str, body: dict[str, Any]) -> tuple[int, str, list[str]]:
        response = _update(e2e, token, {"contract_id": contract_id, **body})
        payload = response.json()["error"]
        return (
            response.status_code,
            payload["code"],
            [d["field"] for d in payload.get("details", [])],
        )

    assert error(analyst, {"rate": "25", "comment": "   "})[:2] == (422, "VALIDATION_ERROR")
    assert "comment" in error(analyst, {"rate": "25", "comment": "   "})[2]
    # Сумму и лимит увеличивают только новой заявкой: такого поля в запросе нет.
    assert error(analyst, {"amount": "3000000.00", "comment": COMMENT})[:2] == (
        422,
        "VALIDATION_ERROR",
    )
    assert error(analyst, {"comment": COMMENT})[1:] == ("VALIDATION_ERROR", ["rate"])
    assert error(analyst, {"rate": "40", "comment": COMMENT})[1:] == ("VALIDATION_ERROR", ["rate"])
    assert error(analyst, {"term_months": 61, "comment": COMMENT})[2] == ["term_months"]
    assert error(analyst, {"rate": "23.5", "comment": COMMENT})[2] == ["rate"]
    assert error(client, {"rate": "25", "comment": COMMENT})[:2] == (403, "FORBIDDEN")

    versions = post(e2e.client, client, "/contracts/versions", {"contract_id": contract_id})
    assert len(versions) == 1
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf"]
