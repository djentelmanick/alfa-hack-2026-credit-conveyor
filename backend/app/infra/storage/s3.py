import asyncio
from collections.abc import Sequence
from typing import Any

import aioboto3
from botocore.config import Config

from app.infra.config.settings import Settings

PROBE_TIMEOUT_SECONDS = 3.0

# Ретраи botocore по умолчанию растягивают ответ /health на десятки секунд, если S3 недоступен.
_PROBE_CONFIG = Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1})


class S3ClientFactory:
    def __init__(self, settings: Settings) -> None:
        self._session = aioboto3.Session()
        self._endpoint = settings.s3_endpoint
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
