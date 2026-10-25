"""Кабинет аналитика через HTTP без БД: роли, обязательный комментарий, формат ответов."""

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.v1.deps import Container
from app.domain.entities import Application, User
from app.domain.enums import SourceType
from tests.fakes.analyst import manual_review_application
from tests.fakes.scenarios import KOLOS
from tests.fakes.uow import InMemoryStore
from tests.integration.api.conftest import bearer

API = "/api/v1/analyst/applications"
COMMENT = "Проверено по отчёту"


@pytest.fixture
async def waiting(store: InMemoryStore, director: User) -> Application:
    app = manual_review_application(
        applicant_id=director.id, inn=KOLOS, reasons=("AMOUNT_OVER_5M",)
    )
    app.company_name = 'ООО "ПЕКАРНЯ КОЛОС"'
    await store.applications.add(app)
    return app


async def _post(
    client: AsyncClient, container: Container, user: User, path: str, body: dict[str, Any]
) -> Any:
    return await client.post(f"{API}{path}", json=body, headers=bearer(container, user))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/list", {}),
        ("/report", {"application_id": str(uuid4())}),
        ("/approve", {"application_id": str(uuid4()), "rate": "23.5", "comment": COMMENT}),
        ("/recheck", {"application_id": str(uuid4()), "comment": COMMENT}),
    ],
)
async def test_client_cannot_use_analyst_endpoints(
    client: AsyncClient, container: Container, director: User, path: str, body: dict[str, Any]
) -> None:
    response = await _post(client, container, director, path, body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_analyst_endpoints_need_token(client: AsyncClient) -> None:
    response = await client.post(f"{API}/list", json={})

    assert response.status_code == 401


@pytest.mark.parametrize("comment", ["", "   "])
@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ("/approve", {"rate": "23.5"}),
        ("/reject", {"reason_code": "TAX_DEBT_OVER_10K"}),
        ("/recheck", {}),
        ("/request-info", {"items": [{"code": "TAX_CERTIFICATE", "message": "Справка"}]}),
        (
            "/counter-offer",
            {
                "options": [
                    {
                        "product_code": "LUMP_SUM",
                        "variant": "UNSECURED",
                        "amount": "1500000.00",
                        "term_months": 24,
                        "rate": "26.5",
                    }
                ]
            },
        ),
    ],
)
async def test_comment_is_required(
    client: AsyncClient,
    container: Container,
    analyst: User,
    waiting: Application,
    store: InMemoryStore,
    path: str,
    extra: dict[str, Any],
    comment: str,
) -> None:
    body = {"application_id": str(waiting.id), "comment": comment} | extra

    response = await _post(client, container, analyst, path, body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["comment"]
    assert waiting.status == "MANUAL_REVIEW"
    assert store.audit.entries == []


async def test_queue_lists_manual_review_with_reasons(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(client, container, analyst, "/list", {"queue": "manual"})

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    (item,) = page["items"]
    assert item["id"] == str(waiting.id)
    assert item["company_name"] == 'ООО "ПЕКАРНЯ КОЛОС"'
    assert item["manual_reasons"] == ["AMOUNT_OVER_5M"]
    assert item["amount"] == "2000000.00"
    assert item["submitted_at"] == "2026-10-24T09:00:00Z"
    assert "risk_class" not in item


async def test_queue_filters_by_inn(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(client, container, analyst, "/list", {"inn": "7700000000"})

    assert response.json()["total"] == 0


async def test_report_of_unknown_application_is_404(
    client: AsyncClient, container: Container, analyst: User
) -> None:
    response = await _post(client, container, analyst, "/report", {"application_id": str(uuid4())})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_report_shape(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(
        client, container, analyst, "/report", {"application_id": str(waiting.id)}
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert set(report) == {
        "application",
        "provided",
        "sources",
        "comparisons",
        "turnover",
        "top_payers",
        "decision",
        "history",
    }
    assert report["application"]["allowed_actions"] == []
    assert report["provided"]["applicant"] == {
        "full_name": "Петров Алексей Николаевич",
        "passport_verified": True,
    }
    assert report["decision"]["manual_reasons"] == ["AMOUNT_OVER_5M"]
    assert [h["to_status"] for h in report["history"]][-1] == "MANUAL_REVIEW"


async def test_approve_with_failed_source_is_409(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    waiting.record_snapshots(manual_review_application(failed=[SourceType.CBR_KYC]).snapshots)

    response = await _post(
        client,
        container,
        analyst,
        "/approve",
        {"application_id": str(waiting.id), "rate": "23.5", "comment": COMMENT},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCES_NOT_READY"


async def test_approve_rate_out_of_range_is_422(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(
        client,
        container,
        analyst,
        "/approve",
        {"application_id": str(waiting.id), "rate": "30", "comment": COMMENT},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "rate"


async def test_request_info_message_reaches_client(
    client: AsyncClient,
    container: Container,
    analyst: User,
    director: User,
    waiting: Application,
) -> None:
    response = await _post(
        client,
        container,
        analyst,
        "/request-info",
        {
            "application_id": str(waiting.id),
            "items": [{"code": "TAX_CERTIFICATE", "message": "Справка об отсутствии долга"}],
            "comment": COMMENT,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "NEEDS_INFO"

    card = await client.post(
        "/api/v1/applications/get",
        json={"application_id": str(waiting.id)},
        headers=bearer(container, director),
    )
    assert card.json()["info_request"]["items"] == [
        {"code": "TAX_CERTIFICATE", "message": "Справка об отсутствии долга"}
    ]
    assert card.json()["allowed_actions"] == ["EDIT", "UPLOAD_STATEMENT", "SUBMIT", "CANCEL"]


async def test_reject_code_must_be_upper_snake(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(
        client,
        container,
        analyst,
        "/reject",
        {"application_id": str(waiting.id), "reason_code": "плохой", "comment": COMMENT},
    )

    assert response.status_code == 422


async def test_recheck_returns_202_checking(
    client: AsyncClient, container: Container, analyst: User, waiting: Application
) -> None:
    response = await _post(
        client,
        container,
        analyst,
        "/recheck",
        {"application_id": str(waiting.id), "comment": COMMENT},
    )

    assert response.status_code == 202
    assert response.json() == {"application_id": str(waiting.id), "status": "CHECKING"}
