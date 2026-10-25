"""Представители (SCOPE, раздел 12): бухгалтер по МЧД, собственник по нотариальной доверенности,
учредитель с МЧД без полномочия на подписание."""

from tests.e2e.conftest import E2E
from tests.e2e.flow import (
    API,
    auth,
    claim_authority,
    create_application,
    error_code,
    login,
    post,
    sign,
    wait_for_status,
)
from tests.fakes.scenarios import KHLEBNY_DVOR, PETROVYKH, TECHPROM

MCHD_BUH = {"kind": "MCHD", "mchd_guid": "5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e01"}
MCHD_LEBEDEV = {"kind": "MCHD", "mchd_guid": "5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e02"}
NOTARY_PETROVA = {
    "kind": "NOTARIAL",
    "notarized_on": "2026-09-15",
    "register_number": "77/123-н/77-2026-4-512",
    "notary_name": "Белова Ирина Александровна",
}


def _approved_and_issued(e2e: E2E, token: str, inn: str) -> dict[str, object]:
    application_id = create_application(e2e.client, token, inn, amount="2000000.00", term_months=12)
    post(e2e.client, token, "/applications/submit", {"application_id": application_id})
    card: dict[str, object] = wait_for_status(e2e.client, token, application_id, "APPROVED")
    post(e2e.client, token, "/applications/accept", {"application_id": application_id})
    sign(e2e.client, token, application_id)
    return card


def test_accountant_with_mchd_gets_approved_and_signs(e2e: E2E) -> None:
    token = login(e2e.client, "buh")
    claimed = claim_authority(e2e.client, token, KHLEBNY_DVOR, "FINANCE_OFFICER", MCHD_BUH)
    assert claimed.status_code == 200, claimed.text

    card = _approved_and_issued(e2e, token, KHLEBNY_DVOR)

    assert card["authority"] == {
        "role": "FINANCE_OFFICER",
        "basis": "POWER_OF_ATTORNEY",
        "representative_name": "Васильева Анна Игоревна",
        "power_of_attorney": f"МЧД {MCHD_BUH['mchd_guid']}",
    }


def test_owner_with_notarial_poa_gets_approved(e2e: E2E) -> None:
    token = login(e2e.client, "petrova")
    claimed = claim_authority(e2e.client, token, PETROVYKH, "OWNER", NOTARY_PETROVA)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["authority"]["registered_founder"] is True

    card = _approved_and_issued(e2e, token, PETROVYKH)

    assert card["authority"]["role"] == "OWNER"


def test_owner_without_signing_power_is_stopped_before_application(e2e: E2E) -> None:
    token = login(e2e.client, "lebedev")

    response = claim_authority(e2e.client, token, TECHPROM, "OWNER", MCHD_LEBEDEV)

    assert error_code(response) == (422, "POA_MISSING_POWER")
    listed = e2e.client.post(
        f"{API}/applications/list", json={"page": 1, "size": 10}, headers=auth(token)
    )
    assert listed.json()["total"] == 0
