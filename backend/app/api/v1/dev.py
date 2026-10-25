from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.deps import AnalystClaims, Container, get_demo_container
from app.api.v1.schemas.common import INN_PATTERN, ROLE_ERRORS, RequestModel
from app.api.v1.schemas.errors import ErrorOut
from app.domain.enums import SourceType

router = APIRouter(prefix="/dev", tags=["dev"])


class MockFailureIn(RequestModel):
    source: SourceType
    inn: str = Field(pattern=INN_PATTERN)
    enabled: bool


class MockFailureOut(BaseModel):
    source: SourceType
    inn: str
    enabled: bool


@router.post(
    "/mocks/failures",
    summary="Режим «сломаться» для мока источника (только DEMO_MODE)",
    responses={**ROLE_ERRORS, 404: {"model": ErrorOut}},
)
async def mock_failures(
    body: MockFailureIn,
    _: AnalystClaims,
    container: Annotated[Container, Depends(get_demo_container)],
) -> MockFailureOut:
    await container.mock_failures.set(body.source, body.inn, enabled=body.enabled)
    return MockFailureOut(source=body.source, inn=body.inn, enabled=body.enabled)
