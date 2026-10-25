import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.api.v1.deps import HealthProbe, get_db_probe, get_s3_probe

router = APIRouter(tags=["service"])

type ProbeStatus = Literal["ok", "error"]


class HealthOut(BaseModel):
    status: ProbeStatus
    db: ProbeStatus
    s3: ProbeStatus


def _as_status(ok: bool) -> ProbeStatus:
    return "ok" if ok else "error"


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Проверка БД и S3",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthOut}},
)
async def health(
    response: Response,
    db_probe: Annotated[HealthProbe, Depends(get_db_probe)],
    s3_probe: Annotated[HealthProbe, Depends(get_s3_probe)],
) -> HealthOut:
    db_ok, s3_ok = await asyncio.gather(db_probe.check(), s3_probe.check())
    if not (db_ok and s3_ok):
        # 503 нужен Docker healthcheck, тело то же, чтобы было видно, что именно упало.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(status=_as_status(db_ok and s3_ok), db=_as_status(db_ok), s3=_as_status(s3_ok))
