"""Выгрузка OpenAPI-схемы в файл `docs/openapi.json`.

Схема лежит в репозитории, чтобы её можно было открыть без запуска бэкенда.
Обновить после правки API (из папки backend):

    docker compose exec -T api python -m app.api.openapi_export > docs/openapi.json
"""

import json
import sys

from fastapi import FastAPI

from app.main import create_app


def render_schema(app: FastAPI) -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    sys.stdout.write(render_schema(create_app()))


if __name__ == "__main__":
    main()
