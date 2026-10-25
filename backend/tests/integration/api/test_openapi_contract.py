"""Схема /openapi.json описывает то, что API реально отдаёт (docs/API.md)."""

from typing import Any

import pytest
from fastapi import FastAPI

ERROR_OUT = "#/components/schemas/ErrorOut"


@pytest.fixture
def schema(app: FastAPI) -> dict[str, Any]:
    return app.openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{method.upper()} {path}", op)
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
    ]


def test_every_422_is_error_out(schema: dict[str, Any]) -> None:
    wrong = [
        name
        for name, op in _operations(schema)
        if "requestBody" in op
        and op["responses"].get("422", {}).get("content", {}).get("application/json", {})
        != {"schema": {"$ref": ERROR_OUT}}
    ]
    assert wrong == []
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_required_fields(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    assert "rate" in components["OfferOptionOut"]["required"]
    assert "token_type" in components["LoginOut"]["required"]
    assert "status" in components["SigningConfirmOut"]["required"]
    assert components["SubmitOut"]["properties"]["status"]["const"] == "SUBMITTED"
    assert components["RecheckOut"]["properties"]["status"]["const"] == "CHECKING"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/applications/get",
        "/api/v1/applications/history",
        "/api/v1/applications/decision",
        "/api/v1/applications/validate",
    ],
)
def test_read_endpoints_do_not_declare_409(schema: dict[str, Any], path: str) -> None:
    assert "409" not in schema["paths"][path]["post"]["responses"]
