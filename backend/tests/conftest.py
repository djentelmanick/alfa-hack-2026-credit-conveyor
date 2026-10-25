"""Общие фикстуры.

PostgreSQL для тестов (database_url): адрес сервера из TEST_DATABASE_URL (по умолчанию postgres
из docker compose). База из адреса пересоздаётся один раз за прогон, поэтому её имя не должно
совпадать с рабочей. Схема строится миграциями Alembic.

S3 для тестов (s3_settings): MinIO из TEST_S3_ENDPOINT (по умолчанию из docker compose).
Тесты пишут в отдельные бакеты *-test, чтобы не задевать файлы локального демо.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.infra.config.settings import Settings
from app.infra.storage.s3 import S3ClientFactory
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return _base_settings()


def _base_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        jwt_secret=SecretStr("x" * 32),
        s3_endpoint="http://localhost:9000",
        s3_public_endpoint="http://localhost:9000",
        s3_access_key=SecretStr("test"),
        s3_secret_key=SecretStr("test"),
        rabbitmq_url="amqp://test:test@localhost:5672/",
        cors_origins=["http://localhost:5173"],
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport не запускает lifespan, поэтому реальные БД и S3 не нужны,
    # а зависимости тесты подменяют через dependency_overrides.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


DEFAULT_URL = "postgresql+asyncpg://app:app@localhost:5432/credit_test"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


async def _recreate_database(url: str) -> None:
    target = make_url(url)
    if target.database in {None, "", "postgres", "credit"}:
        raise RuntimeError(f"TEST_DATABASE_URL указывает на рабочую базу: {target.database}")
    server = create_async_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with server.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await server.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)
    try:
        asyncio.run(_recreate_database(url))
    except OSError as exc:
        pytest.fail(
            f"PostgreSQL для тестов недоступен ({exc}). Запустите `docker compose up postgres` "
            "или задайте TEST_DATABASE_URL.",
            pytrace=False,
        )
    config = Config(str(ALEMBIC_INI))
    # % в URL configparser считает интерполяцией.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    return url


S3_TEST_BUCKETS = {
    "s3_bucket_files": "credit-files-test",
    "s3_bucket_contracts": "credit-contracts-test",
}


async def _ensure_buckets(settings: Settings) -> None:
    async with S3ClientFactory(settings).client() as s3:
        existing = {b["Name"] for b in (await s3.list_buckets())["Buckets"]}
        for bucket in S3_TEST_BUCKETS.values():
            if bucket not in existing:
                await s3.create_bucket(Bucket=bucket)


@pytest.fixture(scope="session")
def s3_test_config() -> dict[str, object]:
    endpoint = os.environ.get("TEST_S3_ENDPOINT", "http://localhost:9000")
    config: dict[str, object] = {
        "s3_endpoint": endpoint,
        "s3_public_endpoint": endpoint,
        "s3_access_key": SecretStr(os.environ.get("TEST_S3_ACCESS_KEY", "minioadmin")),
        "s3_secret_key": SecretStr(os.environ.get("TEST_S3_SECRET_KEY", "minioadmin")),
        **S3_TEST_BUCKETS,
    }
    probe = _base_settings().model_copy(update=config)
    try:
        asyncio.run(_ensure_buckets(probe))
    except (OSError, BotoCoreError, ClientError) as exc:
        pytest.fail(
            f"MinIO для тестов недоступен ({exc}). Запустите `docker compose up minio` "
            "или задайте TEST_S3_ENDPOINT.",
            pytrace=False,
        )
    return config


@pytest.fixture
def s3_settings(settings: Settings, s3_test_config: dict[str, object]) -> Settings:
    return settings.model_copy(update=s3_test_config)
