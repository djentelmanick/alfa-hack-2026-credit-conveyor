import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from functools import cache
from typing import Any, Self
from urllib.parse import quote

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.domain.errors import NotFound, StorageUnavailable
from app.domain.ports import FileStoragePort, StoredFile
from app.infra.config.settings import Settings

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3.0

# Ретраи botocore по умолчанию растягивают ответ /health на десятки секунд, если S3 недоступен.
_PROBE_CONFIG = Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1})
# Клиент ждёт ответа на загрузку выписки: лучше быстро отдать 503, чем висеть минуту.
# s3v4 явно: иначе presigned-ссылка подписывается по SigV2 без X-Amz-Expires.
_STORAGE_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 2},
    signature_version="s3v4",
)
_MISSING_KEY_CODES = frozenset({"NoSuchKey", "404"})


@cache
def _shared_session() -> aioboto3.Session:
    """Сессия загружает модели сервисов botocore, это секунды: одна на процесс."""
    return aioboto3.Session()


class S3ClientFactory:
    def __init__(self, settings: Settings, *, endpoint: str | None = None) -> None:
        self._session = _shared_session()
        self._endpoint = endpoint or settings.s3_endpoint
        self._region = settings.s3_region
        self._access_key = settings.s3_access_key.get_secret_value()
        self._secret_key = settings.s3_secret_key.get_secret_value()

    def client(self, config: Config | None = None) -> Any:
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            config=config,
        )


class S3Probe:
    def __init__(self, factory: S3ClientFactory, buckets: Sequence[str]) -> None:
        self._factory = factory
        self._buckets = tuple(buckets)

    async def check(self) -> bool:
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                async with self._factory.client(_PROBE_CONFIG) as s3:
                    for bucket in self._buckets:
                        await s3.head_bucket(Bucket=bucket)
        except Exception:
            return False
        return True


class S3FileStorage(FileStoragePort):
    """Ссылки подписываются отдельным клиентом на публичный адрес: внутренний (minio:9000)
    браузеру недоступен, а подпись привязана к хосту. Подпись считается локально, без сети.
    """

    def __init__(self, factory: S3ClientFactory, public_factory: S3ClientFactory) -> None:
        self._factory = factory
        self._public_factory = public_factory

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            S3ClientFactory(settings),
            S3ClientFactory(settings, endpoint=settings.s3_public_endpoint),
        )

    async def put(self, bucket: str, key: str, data: bytes, content_type: str) -> StoredFile:
        async with self._client() as s3:
            await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return StoredFile(bucket=bucket, key=key, size=len(data), content_type=content_type)

    async def get(self, bucket: str, key: str) -> bytes:
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING_KEY_CODES:
                    raise NotFound("Файл не найден") from None
                raise
            async with response["Body"] as stream:
                data: bytes = await stream.read()
        return data

    async def delete(self, bucket: str, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=bucket, Key=key)

    async def presigned_get(self, bucket: str, key: str, ttl_sec: int, file_name: str) -> str:
        async with self._client(public=True) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                    "ResponseContentDisposition": (
                        f"attachment; filename*=UTF-8''{quote(file_name)}"
                    ),
                },
                ExpiresIn=ttl_sec,
            )
        return url

    @asynccontextmanager
    async def _client(self, *, public: bool = False) -> AsyncIterator[Any]:
        factory = self._public_factory if public else self._factory
        try:
            async with factory.client(_STORAGE_CONFIG) as s3:
                yield s3
        except NotFound:
            raise
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            logger.warning("S3 недоступен: %s", exc)
            raise StorageUnavailable() from exc
