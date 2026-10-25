"""Эмуляция времени договора (SCOPE, раздел 8): сброс, права и независимость от сроков заявок."""

from tests.e2e.conftest import ANALYST, E2E, contract_files
from tests.e2e.flow import (
    API,
    IE_CONSENTS,
    auth,
    create_application,
    error_code,
    issue_kolos_loan,
    login,
    post,
    wait_for_status,
)
from tests.fakes.scenarios import SOKOLOV


def test_reset_returns_contract_to_issue(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    contract_id = issue_kolos_loan(e2e.client, token)
    contract = {"contract_id": contract_id}
    partial = contract | {"kind": "PARTIAL", "amount": "100000.00"}
    for _ in range(2):
        post(e2e.client, token, "/contracts/emulation/next-month", contract)
    post(e2e.client, token, "/contracts/repay", partial)

    reset = post(e2e.client, token, "/contracts/emulation/reset", contract)

    assert (reset["version_no"], reset["contract_date"]) == (1, "2026-10-24")
    assert (reset["status"], reset["outstanding"]) == ("ACTIVE", "2000000.00")
    journal = post(e2e.client, token, "/contracts/operations", contract)
    assert [op["type"] for op in journal["items"]] == ["DISBURSEMENT"]
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf"]

    again = post(e2e.client, token, "/contracts/repay", partial)
    assert again["version_no"] == 2
    assert contract_files(e2e, contract_id) == [f"{contract_id}/v1.pdf", f"{contract_id}/v2.pdf"]


def test_emulation_does_not_touch_offer_deadlines(e2e: E2E) -> None:
    sokolov = login(e2e.client, "sokolov")
    application_id = create_application(
        e2e.client,
        sokolov,
        SOKOLOV,
        consents=IE_CONSENTS,
        amount="4500000.00",
        term_months=24,
        owner_guarantee_consent=False,
    )
    post(e2e.client, sokolov, "/applications/submit", {"application_id": application_id})
    before = wait_for_status(e2e.client, sokolov, application_id, "COUNTER_OFFER")

    kolos = login(e2e.client, "kolos")
    contract_id = issue_kolos_loan(e2e.client, kolos)
    post(
        e2e.client,
        kolos,
        "/contracts/emulation/rewind",
        {"contract_id": contract_id, "target_date": "2027-09-24"},
    )
    # Перемотка на 11 месяцев не должна сдвинуть реальное время, по которому истекают предложения.
    portal = e2e.client.portal
    assert portal is not None
    assert portal.call(e2e.expiration.run_once) == 0

    after = post(e2e.client, sokolov, "/applications/get", {"application_id": application_id})
    assert (after["status"], after["expires_at"]) == ("COUNTER_OFFER", before["expires_at"])


def test_only_owner_operates_contract(e2e: E2E) -> None:
    kolos = login(e2e.client, "kolos")
    contract_id = issue_kolos_loan(e2e.client, kolos)
    full = {"contract_id": contract_id, "kind": "FULL"}

    stranger = login(e2e.client, "sokolov")
    analyst = login(e2e.client, ANALYST)
    for token in (stranger, analyst):
        response = e2e.client.post(f"{API}/contracts/repay", json=full, headers=auth(token))
        assert error_code(response) == (403, "FORBIDDEN")
    foreign_shift = e2e.client.post(
        f"{API}/contracts/emulation/next-month",
        json={"contract_id": contract_id},
        headers=auth(stranger),
    )
    assert error_code(foreign_shift) == (403, "FORBIDDEN")


def test_rewind_rejects_past_and_after_end(e2e: E2E) -> None:
    token = login(e2e.client, "kolos")
    contract_id = issue_kolos_loan(e2e.client, token)

    def rewind(target: str) -> tuple[int, str]:
        return error_code(
            e2e.client.post(
                f"{API}/contracts/emulation/rewind",
                json={"contract_id": contract_id, "target_date": target},
                headers=auth(token),
            )
        )

    assert rewind("2026-10-23") == (422, "DATE_IN_PAST")
    assert rewind("2027-10-25") == (422, "DATE_AFTER_END")
