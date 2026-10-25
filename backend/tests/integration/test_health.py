from fastapi import FastAPI
from httpx import AsyncClient

from app.api.v1.deps import get_db_probe, get_s3_probe


class StubProbe:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def check(self) -> bool:
        return self._ok


async def test_health_reports_ok_when_db_and_s3_respond(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_db_probe] = lambda: StubProbe(ok=True)
    app.dependency_overrides[get_s3_probe] = lambda: StubProbe(ok=True)

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "s3": "ok"}
