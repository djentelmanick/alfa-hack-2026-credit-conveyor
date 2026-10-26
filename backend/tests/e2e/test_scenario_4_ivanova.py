"""SCOPE раздел 12, сценарий 4: ИП Иванова, кофейня, 3 млн на 24 мес без залога, счёт не у нас.

Выписка за 4 месяца даёт дозапрос, выписка за 12 месяцев с ЭП банка подтверждает выручку,
класс B (50 баллов) даёт одобрение по 26,5%. Подписание проверяется в сценариях 1, 3 и 5.
"""

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from app.infra.storage.s3 import S3ClientFactory
from tests.e2e.conftest import ANALYST, E2E
from tests.e2e.flow import (
    IE_CONSENTS,
    auth,
    connect,
    create_application,
    login,
    post,
    read_until_status,
    statuses,
    steps,
    upload_statement,
    wait_for_status,
)
from tests.fakes.scenarios import IVANOVA

STATEMENTS_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "infra" / "mocks" / "data" / "statements"
)
STATEMENT_4M = (STATEMENTS_DIR / "ivanova_4m.txt").read_bytes()
STATEMENT_12M = (STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes()
SOURCE_STEPS = ["EGRUL", "BKI", "TURNOVER", "FNS", "FSSP", "CBR_KYC", "ROSFIN", "STATEMENT"]
# FixedClock стоит на 2026-10-24 09:00 UTC.
APPROVED_EXPIRES_AT = "2026-10-31T09:00:00Z"
LINK_EXPIRES_AT = "2026-10-24T09:05:00Z"
MAX_BYTES = 10 * 1024 * 1024


def _draft(e2e: E2E, token: str) -> str:
    return create_application(
        e2e.client,
        token,
        IVANOVA,
        consents=IE_CONSENTS,
        amount="3000000.00",
        term_months=24,
        owner_guarantee_consent=False,
    )


def _s3_object(e2e: E2E, key: str) -> bytes | None:
    settings = e2e.settings

    async def read() -> bytes | None:
        async with S3ClientFactory(settings).client() as s3:
            listed = await s3.list_objects_v2(Bucket=settings.s3_bucket_files, Prefix=key)
            if not listed.get("Contents"):
                return None
            response = await s3.get_object(Bucket=settings.s3_bucket_files, Key=key)
            async with response["Body"] as stream:
                data: bytes = await stream.read()
            return data

    return asyncio.run(read())


def _s3_keys(e2e: E2E, prefix: str) -> list[str]:
    settings = e2e.settings

    async def keys() -> list[str]:
        async with S3ClientFactory(settings).client() as s3:
            listed = await s3.list_objects_v2(Bucket=settings.s3_bucket_files, Prefix=prefix)
            return [item["Key"] for item in listed.get("Contents", [])]

    return asyncio.run(keys())


def test_ivanova_needs_info_then_approved_at_26_5(e2e: E2E) -> None:
    token = login(e2e.client, "ivanova")
    with connect(e2e.client, token) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        application_id = _draft(e2e, token)

        short = upload_statement(e2e.client, token, application_id, "ivanova_4m.txt", STATEMENT_4M)
        assert short["months_covered"] == 4
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        card = wait_for_status(e2e.client, token, application_id, "NEEDS_INFO")
        first = read_until_status(ws, application_id, "NEEDS_INFO")

        assert statuses(first) == ["SUBMITTED", "CHECKING", "NEEDS_INFO"]
        assert steps(first) == SOURCE_STEPS
        assert first[-1]["reason"] == "STATEMENT_PERIOD"
        assert [item["code"] for item in card["info_request"]["items"]] == ["STATEMENT"]
        assert card["allowed_actions"] == ["EDIT", "UPLOAD_STATEMENT", "SUBMIT", "CANCEL"]

        full = upload_statement(
            e2e.client, token, application_id, "ivanova_12m_signed.txt", STATEMENT_12M
        )
        assert (full["months_covered"], full["signature_status"]) == (12, "VALID")
        assert full["annual_revenue"] == "15000000.00"
        post(e2e.client, token, "/applications/submit", {"application_id": application_id})
        wait_for_status(e2e.client, token, application_id, "APPROVED")
        second = read_until_status(ws, application_id, "APPROVED")

    assert statuses(second) == ["SUBMITTED", "CHECKING", "SCORING", "APPROVED"]
    assert steps(second) == SOURCE_STEPS
    assert second[0]["from_status"] == "NEEDS_INFO"
    assert second[-1]["expires_at"] == APPROVED_EXPIRES_AT

    decision = post(e2e.client, token, "/applications/decision", {"application_id": application_id})
    assert decision["outcome"] == "APPROVED"
    assert decision["rate"] == "26.5"
    assert "scoring" not in decision
    report = post(
        e2e.client,
        login(e2e.client, ANALYST),
        "/analyst/applications/report",
        {"application_id": application_id},
    )
    assert (report["scoring"]["total"], report["scoring"]["risk_class"]) == (50, "B")

    card = post(e2e.client, token, "/applications/get", {"application_id": application_id})
    assert "info_request" not in card
    assert [s["months_covered"] for s in card["statements"]] == [4, 12]


def test_statement_original_lies_in_s3_under_spec_key(e2e: E2E) -> None:
    token = login(e2e.client, "ivanova")
    application_id = _draft(e2e, token)

    statement = upload_statement(
        e2e.client, token, application_id, "ivanova_12m_signed.txt", STATEMENT_12M
    )

    key = f"statements/{application_id}/{statement['id']}.txt"
    assert _s3_object(e2e, key) == STATEMENT_12M


def test_presigned_url_opens_without_token(e2e: E2E) -> None:
    token = login(e2e.client, "ivanova")
    application_id = _draft(e2e, token)
    statement = upload_statement(
        e2e.client, token, application_id, "ivanova_12m_signed.txt", STATEMENT_12M
    )

    link = post(
        e2e.client,
        token,
        "/applications/statements/download-url",
        {"application_id": application_id, "statement_id": statement["id"]},
    )

    assert link["file_name"] == "ivanova_12m_signed.txt"
    assert link["expires_at"] == LINK_EXPIRES_AT
    # Срок жизни ссылки задаёт S3 по X-Amz-Expires; само истечение проверяет
    # tests/integration/storage/test_s3_storage.py::test_presigned_link_expires.
    assert parse_qs(urlsplit(link["url"]).query)["X-Amz-Expires"] == ["300"]
    response = httpx.get(link["url"])
    assert response.status_code == 200
    assert response.content == STATEMENT_12M


def test_other_client_gets_403_for_download_url(e2e: E2E) -> None:
    owner = login(e2e.client, "ivanova")
    application_id = _draft(e2e, owner)
    statement = upload_statement(
        e2e.client, owner, application_id, "ivanova_12m_signed.txt", STATEMENT_12M
    )
    stranger = login(e2e.client, "kolos")

    response = e2e.client.post(
        "/api/v1/applications/statements/download-url",
        json={"application_id": application_id, "statement_id": statement["id"]},
        headers=auth(stranger),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_file_over_10mb_gets_413(e2e: E2E) -> None:
    token = login(e2e.client, "ivanova")
    application_id = _draft(e2e, token)

    response = e2e.client.post(
        "/api/v1/applications/statements/upload",
        data={"application_id": application_id},
        files={"file": ("big.txt", b"x" * (MAX_BYTES + 1), "text/plain")},
        headers=auth(token),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert _s3_keys(e2e, f"statements/{application_id}/") == []
