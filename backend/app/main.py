import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.v1 import health
from app.api.v1.deps import Container
from app.infra.config.settings import Settings, get_settings
from app.infra.db.session import DatabaseProbe, create_engine
from app.infra.storage.s3 import S3ClientFactory, S3Probe

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        s3_factory = S3ClientFactory(settings)
        app.state.container = Container(
            db_probe=DatabaseProbe(engine),
            s3_probe=S3Probe(s3_factory, [settings.s3_bucket_files, settings.s3_bucket_contracts]),
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Credit Conveyor API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Токен передаётся в заголовке Authorization, куки не используются.
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    app.include_router(api)
    return app
