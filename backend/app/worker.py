"""Точка входа процесса worker: `python -m app.worker` (BACKEND_SPEC, раздел 11)."""

import asyncio
import logging
import signal

from app.infra.config.settings import get_settings
from app.infra.db.session import create_engine
from app.main import build_worker, configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    worker = build_worker(settings, engine)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    # docker compose stop шлёт SIGTERM: даём закончить шаг и закрыть соединения,
    # неподтверждённые задачи брокер доставит заново.
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        await worker.start()
        logger.info("Worker запущен")
        await stopping.wait()
        logger.info("Worker останавливается")
    finally:
        await worker.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
