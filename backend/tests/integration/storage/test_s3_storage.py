"""S3FileStorage на настоящем MinIO (BACKEND_SPEC, раздел 14)."""

import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from app.domain.errors import NotFound, StorageUnavailable
from app.infra.config.settings import Settings
from app.infra.storage.s3 import S3FileStorage


@pytest.fixture
def storage(s3_settings: Settings) -> S3FileStorage:
    return S3FileStorage.from_settings(s3_settings)


@pytest.fixture
def key() -> str:
    return f"tests/{uuid4()}.txt"


async def test_put_get_delete_roundtrip(
    storage: S3FileStorage, s3_settings: Settings, key: str
) -> None:
    bucket = s3_settings.s3_bucket_files
    stored = await storage.put(bucket, key, b"1CClientBankExchange", "text/plain")

    assert (stored.bucket, stored.key, stored.size) == (bucket, key, 20)
    assert await storage.get(bucket, key) == b"1CClientBankExchange"

    await storage.delete(bucket, key)
    with pytest.raises(NotFound):
        await storage.get(bucket, key)


async def test_presigned_link_opens_without_credentials(
    storage: S3FileStorage, s3_settings: Settings, key: str
) -> None:
    bucket = s3_settings.s3_bucket_files
    await storage.put(bucket, key, b"statement", "text/plain")

    url = await storage.presigned_get(bucket, key, 300, "выписка.txt")

    assert url.startswith(s3_settings.s3_public_endpoint)
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["300"]
    async with httpx.AsyncClient() as http:
        response = await http.get(url)
    assert response.status_code == 200
    assert response.content == b"statement"
    assert "attachment" in response.headers["content-disposition"]


async def test_presigned_link_expires(
    storage: S3FileStorage, s3_settings: Settings, key: str
) -> None:
    bucket = s3_settings.s3_bucket_files
    await storage.put(bucket, key, b"statement", "text/plain")
    url = await storage.presigned_get(bucket, key, 1, "statement.txt")

    await asyncio.sleep(2)

    async with httpx.AsyncClient() as http:
        response = await http.get(url)
    assert response.status_code == 403


async def test_unreachable_s3_raises_storage_unavailable(s3_settings: Settings, key: str) -> None:
    broken = S3FileStorage.from_settings(
        s3_settings.model_copy(update={"s3_endpoint": "http://127.0.0.1:1"})
    )

    with pytest.raises(StorageUnavailable):
        await broken.put(s3_settings.s3_bucket_files, key, b"x", "text/plain")
