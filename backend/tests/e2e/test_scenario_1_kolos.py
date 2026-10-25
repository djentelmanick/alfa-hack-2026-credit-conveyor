"""SCOPE раздел 12, сценарий 1: ООО «Пекарня Колос», 2 млн на 12 мес без залога.

Класс A (90 баллов) даёт одобрение по 23,5%, клиент подписывает кодом и получает кредит.
"""

import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from starlette.testclient import WebSocketTestSession

from tests.e2e.conftest import E2E, contract_files
from tests.e2e.flow import (
    API,
    auth,
    connect,
    create_application,
    download,
    error_code,
    issue_kolos_loan,
    login,
    post,
    read_until_status,
    sign,
    statuses,
    steps,
    wait_for_status,
)
from tests.fakes.scenarios import KOLOS

SOURCES_IN_ORDER = ["EGRUL", "BKI", "TURNOVER", "FNS", "FSSP", "CBR_KYC", "ROSFIN"]
# FixedClock стоит на 2026-10-24 09:00 UTC, одобрение действует 7 дней.
APPROVED_EXPIRES_AT = "2026-10-31T09:00:00Z"
LINK_EXPIRES_AT = "2026-10-24T09:05:00Z"


def _submit_and_wait_approval(
    e2e: E2E, ws: WebSocketTestSession, token: str
) -> tuple[str, list[Any]]:
    application_id = create_application(
        e2e.client, token, KOLOS, amount="2000000.00", term_months=12
    )
    snapshot = ws.receive_json()
    assert snapshot["type"] == "snapshot"

    submitted = post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    assert submitted == {"application_id": application_id, "status": "SUBMITTED"}
    wait_for_status(e2e.client, token, application_id, "APPROVED")
    return application_id, read_until_status(ws, application_id, "APPROVED")


def test_kolos_is_approved_at_23_5_with_live_statuses(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    with connect(e2e.client, token) as ws:
        assert ws.accepted_subprotocol == "bearer"
        application_id, messages = _submit_and_wait_approval(e2e, ws, token)

    assert statuses(messages) == ["SUBMITTED", "CHECKING", "SCORING", "APPROVED"]
    kinds = [m["type"] for m in messages]
    first_step = kinds.index("application.step")
    last_step = len(kinds) - 1 - kinds[::-1].index("application.step")
    # Шаги источников идут строго между CHECKING и SCORING.
    assert statuses(messages[:first_step]) == ["SUBMITTED", "CHECKING"]
    assert statuses(messages[last_step:]) == ["SCORING", "APPROVED"]
    assert steps(messages) == SOURCES_IN_ORDER

    approved = messages[-1]
    assert approved["from_status"] == "SCORING"
    assert approved["is_final"] is False
    assert approved["expires_at"] == APPROVED_EXPIRES_AT

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    assert decision["outcome"] == "APPROVED"
    assert decision["rate"] == "23.5"
    assert (decision["scoring"]["total"], decision["scoring"]["risk_class"]) == (90, "A")
    assert decision["expires_at"] == APPROVED_EXPIRES_AT

    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert card["status_label"] == "Одобрена"
    assert card["allowed_actions"] == ["ACCEPT", "CANCEL"]
    assert card["company"] == {"name": 'ООО "ПЕКАРНЯ КОЛОС"', "kind": "LLC", "okved": "56.10"}


def test_approval_is_cancelled_after_7_days(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    portal = e2e.client.portal
    assert portal is not None
    expiration = e2e.expiration

    with connect(e2e.client, token) as ws:
        application_id, _ = _submit_and_wait_approval(e2e, ws, token)

        e2e.clock.advance(timedelta(days=7) - timedelta(seconds=1))
        assert portal.call(expiration.run_once) == 0

        e2e.clock.advance(timedelta(seconds=1))
        assert portal.call(expiration.run_once) == 1
        (cancelled,) = read_until_status(ws, application_id, "CANCELLED")

    assert cancelled["from_status"] == "APPROVED"
    assert cancelled["reason"] == "OFFER_EXPIRED"
    assert cancelled["is_final"] is True
    assert "expires_at" not in cancelled


def test_kolos_signs_with_code_and_gets_the_loan(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    with connect(e2e.client, token) as ws:
        application_id, _ = _submit_and_wait_approval(e2e, ws, token)
        accepted = post(
            e2e.client, token, "/applications/accept", {"application_id": application_id}
        )
        assert (accepted["status"], accepted["allowed_actions"]) == ("SIGNING", ["SIGN", "CANCEL"])

        code = post(
            e2e.client, token, "/applications/signing/code", {"application_id": application_id}
        )
        assert re.fullmatch(r"\d{6}", code["code"])
        assert code["expires_at"] == LINK_EXPIRES_AT
        wrong = e2e.client.post(
            f"{API}/applications/signing/confirm",
            json={"application_id": application_id, "code": "x" + code["code"][1:]},
            headers=auth(token),
        )
        assert wrong.status_code == 400
        assert wrong.json()["error"]["code"] == "BAD_SIGNING_CODE"

        contract_id = sign(e2e.client, token, application_id)
        issued = read_until_status(ws, application_id, "ISSUED")[-1]

    assert (issued["from_status"], issued["is_final"]) == ("SIGNING", True)
    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert (card["status"], card["contract_id"]) == ("ISSUED", contract_id)

    contract = post(e2e.client, token, "/contracts/get", {"contract_id": contract_id})
    assert re.fullmatch(r"КД-2026-\d{6}", contract["number"])
    assert {
        k: contract[k] for k in ("product_code", "variant", "status", "rate", "term_months")
    } == {
        "product_code": "LUMP_SUM",
        "variant": "UNSECURED",
        "status": "ACTIVE",
        "rate": "23.5",
        "term_months": 12,
    }
    assert (contract["principal"], contract["outstanding"]) == ("2000000.00", "2000000.00")
    assert contract["contract_date"] == "2026-10-24"
    assert contract["version_no"] == 1
    assert contract["document"] == {"version_no": 1, "file_name": f"{contract['number']}_v1.pdf"}
    assert contract["next_payment"]["date"] == "2026-11-24"
    assert contract["allowed_operations"] == ["PARTIAL_PREPAYMENT", "FULL_REPAYMENT"]
    assert "limit" not in contract

    schedule = post(e2e.client, token, "/contracts/schedule", {"contract_id": contract_id})
    assert len(schedule) == 12
    assert (schedule[0]["n"], schedule[0]["due_date"]) == (1, "2026-11-24")
    assert schedule[-1]["due_date"] == "2027-10-24"
    assert schedule[-1]["balance"] == "0.00"
    assert sum(Decimal(item["principal"]) for item in schedule) == Decimal("2000000.00")
    assert schedule[0]["payment"] == contract["next_payment"]["amount"]

    operations = post(e2e.client, token, "/contracts/operations", {"contract_id": contract_id})
    (disbursement,) = operations["items"]
    assert (disbursement["type"], disbursement["amount"], disbursement["actor"]) == (
        "DISBURSEMENT",
        "2000000.00",
        "SYSTEM",
    )

    (version,) = post(e2e.client, token, "/contracts/versions", {"contract_id": contract_id})
    assert version["changes"] == []
    assert version["has_document"] is True
    assert version["reason"] == "Договор подписан"
    assert (version["author"]["role"], version["author"]["name"]) == (
        "CLIENT",
        "Петров Алексей Николаевич",
    )

    link = post(e2e.client, token, "/contracts/document-url", {"contract_id": contract_id})
    assert (link["version_no"], link["expires_at"]) == (1, LINK_EXPIRES_AT)
    assert download(link["url"]).startswith(b"%PDF")
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf"]

    (listed,) = post(e2e.client, token, "/contracts/list", {})
    assert (listed["id"], listed["outstanding"]) == (contract_id, "2000000.00")

    stranger = login(e2e.client, "romashka")
    foreign = e2e.client.post(
        f"{API}/contracts/get", json={"contract_id": contract_id}, headers=auth(stranger)
    )
    assert foreign.status_code == 403
    assert post(e2e.client, stranger, "/contracts/list", {}) == []


def test_kolos_three_months_prepayment_and_full_rewind(e2e: E2E) -> None:
    """SCOPE раздел 12, после выдачи: плановые платежи эмуляцией и частичное досрочное погашение."""
    token = login(e2e.client, "kolos")
    contract_id = issue_kolos_loan(e2e.client, token)
    contract = {"contract_id": contract_id}

    for _ in range(3):
        shifted = post(e2e.client, token, "/contracts/emulation/next-month", contract)
    assert shifted["contract_date"] == "2027-01-24"
    assert [op["type"] for op in shifted["operations"]] == ["SCHEDULED_PAYMENT"]
    journal = post(e2e.client, token, "/contracts/operations", contract | {"size": 100})
    payments = [op for op in journal["items"] if op["type"] == "SCHEDULED_PAYMENT"]
    assert len(payments) == 3
    assert {op["actor"] for op in payments} == {"EMULATOR"}
    old_payment = Decimal(shifted["contract"]["next_payment"]["amount"])
    debt = Decimal(shifted["contract"]["outstanding"])

    repaid = post(
        e2e.client, token, "/contracts/repay", contract | {"kind": "PARTIAL", "amount": "500000.00"}
    )
    assert repaid["version_no"] == 2
    assert Decimal(repaid["outstanding"]) == debt - Decimal(500000)
    assert Decimal(repaid["next_payment"]["amount"]) < old_payment
    schedule = post(e2e.client, token, "/contracts/schedule", contract)
    assert (len(schedule), schedule[-1]["due_date"]) == (9, "2027-10-24")
    version = post(e2e.client, token, "/contracts/versions", contract)[-1]
    assert version["reason"] == "Частичное досрочное погашение"
    assert [c["field"] for c in version["changes"]] == ["outstanding"]
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf", f"{contract_id}/v2.pdf"]

    closed = post(
        e2e.client, token, "/contracts/emulation/rewind", contract | {"target_date": "2027-10-24"}
    )
    assert closed["contract_date"] == "2027-10-24"
    assert (closed["contract"]["status"], closed["contract"]["outstanding"]) == ("CLOSED", "0.00")
    assert closed["contract"]["allowed_operations"] == []
    after_close = e2e.client.post(
        f"{API}/contracts/emulation/next-month", json=contract, headers=auth(token)
    )
    assert error_code(after_close) == (409, "INVALID_STATUS")
