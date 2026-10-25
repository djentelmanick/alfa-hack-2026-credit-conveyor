"""e2e через HTTP и WebSocket на настоящих PostgreSQL и MinIO (TEST_DATABASE_URL, TEST_S3_ENDPOINT).

Приложение поднимается целиком через lifespan (TestClient), меняются только часы
и скоринг: FixedScoringEngine по SCOPE раздел 12.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.deps import Container, Lifecycle
from app.application.expiration_job import ExpirationJob
from app.domain.entities import UserCompany
from app.domain.enums import CompanyKind, Role
from app.domain.ports import UnitOfWork
from app.domain.scoring import ScoringModel
from app.infra.config.catalog import load_catalog
from app.infra.config.settings import Settings
from app.infra.db.mock_failures import SqlMockFailures
from app.infra.db.models import Base
from app.infra.db.session import create_session_factory
from app.infra.db.uow import SqlUnitOfWork
from app.infra.storage.s3 import S3ClientFactory
from app.main import (
    ApiMessagingPorts,
    EventSink,
    build_container,
    build_mocks,
    build_pipeline,
    create_app,
)
from tests.fakes.auth import FixedClock, make_user
from tests.fakes.runtime import InMemoryEventBus, InProcessTaskQueue
from tests.fakes.scenarios import IVANOVA, KOLOS, ROMASHKA, SOKOLOV, TECHPROM
from tests.fakes.scoring import FixedScoringEngine

# Логин, ФИО руководителя по ЕГРЮЛ, компания, форма бизнеса.
CLIENTS = {
    "kolos": ("Петров Алексей Николаевич", KOLOS, 'ООО "ПЕКАРНЯ КОЛОС"', CompanyKind.LLC),
    "romashka": ("Смирнова Ольга Викторовна", ROMASHKA, 'ООО "РОМАШКА"', CompanyKind.LLC),
    "sokolov": (
        "Соколов Дмитрий Андреевич",
        SOKOLOV,
        "ИП СОКОЛОВ ДМИТРИЙ АНДРЕЕВИЧ",
        CompanyKind.IE,
    ),
    "ivanova": ("Иванова Елена Сергеевна", IVANOVA, "ИП ИВАНОВА ЕЛЕНА СЕРГЕЕВНА", CompanyKind.IE),
    "techprom": ("Кузнецов Игорь Валентинович", TECHPROM, 'ООО "ТЕХПРОМ"', CompanyKind.LLC),
}
ANALYST = "analyst"


@dataclass(frozen=True, slots=True)
class E2E:
    client: TestClient
    container: Container
    clock: FixedClock
    settings: Settings
    expiration: ExpirationJob


async def _reset_and_seed(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        await _truncate(engine)
        session_factory = create_session_factory(engine)
        async with SqlUnitOfWork(session_factory) as uow:
            for login, (full_name, inn, name, kind) in CLIENTS.items():
                company = UserCompany(inn=inn, name=name, kind=kind, is_director=True)
                await uow.users.add(
                    await make_user(login=login, full_name=full_name, companies=(company,))
                )
            await uow.users.add(
                await make_user(login=ANALYST, role=Role.ANALYST, full_name="Орлова Мария Игоревна")
            )
            await uow.commit()
    finally:
        await engine.dispose()


async def _truncate(engine: AsyncEngine) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


def audit_actions(e2e: E2E, application_id: str) -> list[tuple[str, dict[str, str]]]:
    """Аудит читается из БД напрямую: в API его нет, он виден аналитику через отчёт."""

    async def read() -> list[tuple[str, dict[str, str]]]:
        engine = create_async_engine(e2e.settings.database_url, poolclass=NullPool)
        try:
            async with SqlUnitOfWork(create_session_factory(engine)) as uow:
                entries = await uow.audit.list_for(UUID(application_id))
        finally:
            await engine.dispose()
        return [(e.action, dict(e.details)) for e in entries]

    return asyncio.run(read())


def contract_files(e2e: E2E, contract_id: str) -> list[str]:
    """Ключи PDF договора в бакете договоров."""
    settings = e2e.settings

    async def keys() -> list[str]:
        async with S3ClientFactory(settings).client() as s3:
            listed = await s3.list_objects_v2(
                Bucket=settings.s3_bucket_contracts, Prefix=f"{contract_id}/"
            )
            return sorted(item["Key"] for item in listed.get("Contents", []))

    return asyncio.run(keys())


@pytest.fixture
def e2e_settings(s3_settings: Settings, database_url: str) -> Settings:
    return s3_settings.model_copy(
        update={
            "database_url": database_url,
            "demo_mode": True,
            "pipeline_step_delay_ms": 0,
            "source_retry_pause_ms": 0,
        }
    )


@pytest.fixture
def scoring() -> ScoringModel:
    return FixedScoringEngine()


@dataclass(frozen=True, slots=True)
class _InProcessWorker:
    queue: InProcessTaskQueue

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self.queue.shutdown()


@pytest.fixture
def e2e(e2e_settings: Settings, scoring: ScoringModel) -> Iterator[E2E]:
    asyncio.run(_reset_and_seed(e2e_settings.database_url))
    clock = FixedClock()
    expirations: list[ExpirationJob] = []

    def factory(settings: Settings, engine: AsyncEngine) -> Container:
        # Конвейер worker крутится в процессе api: вместо RabbitMQ in-memory очередь и шина.
        bus, queue = InMemoryEventBus(), InProcessTaskQueue()
        session_factory = create_session_factory(engine)

        def uow() -> UnitOfWork:
            return SqlUnitOfWork(session_factory)

        mocks = build_mocks(settings, SqlMockFailures(session_factory))
        pipeline = build_pipeline(
            settings,
            uow=uow,
            engine=engine,
            sources=mocks.sources,
            catalog=load_catalog(),
            publisher=bus,
            clock=clock,
            scoring=scoring,
        )
        queue.bind(pipeline.run)
        expirations.append(ExpirationJob(uow=uow, publisher=bus, clock=clock))

        def subscribe(deliver: EventSink) -> Lifecycle:
            bus.subscribe(deliver)
            return _InProcessWorker(queue)

        messaging = ApiMessagingPorts(queue=queue, publisher=bus, subscribe=subscribe)
        return build_container(settings, engine, clock=clock, messaging=messaging)

    app = create_app(e2e_settings, container_factory=factory)
    with TestClient(app) as client:
        yield E2E(
            client=client,
            container=app.state.container,
            clock=clock,
            settings=e2e_settings,
            expiration=expirations[-1],
        )
