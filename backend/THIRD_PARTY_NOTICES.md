# Сторонние компоненты бэкенда

Точные версии Python-пакетов, включая транзитивные, зафиксированы в `uv.lock`.

## Python-пакеты

| Пакет | Лицензия | Назначение |
|---|---|---|
| fastapi | MIT | веб-фреймворк |
| uvicorn | BSD-3-Clause | ASGI-сервер |
| pydantic | MIT | DTO и валидация |
| pydantic-settings | MIT | настройки из окружения |
| sqlalchemy | MIT | ORM, async |
| asyncpg | Apache-2.0 | драйвер PostgreSQL |
| alembic | MIT | миграции |
| aioboto3 | Apache-2.0 | клиент S3 |
| pyjwt | MIT | JWT |
| argon2-cffi | MIT | хеширование паролей |
| pytest | MIT | тесты (dev) |
| pytest-asyncio | Apache-2.0 | async-тесты (dev) |
| httpx | BSD-3-Clause | HTTP-клиент для тестов (dev) |
| ruff | MIT | линтер и форматтер (dev) |
| mypy | MIT | проверка типов (dev) |

## Образы и инструменты

| Компонент | Версия | Лицензия | Назначение |
|---|---|---|---|
| python (Docker official image) | `3.15.1-slim` | PSF-2.0 | базовый образ `api` |
| uv (`ghcr.io/astral-sh/uv`) | `0.13.5` | MIT / Apache-2.0 | установка зависимостей |
| postgres (Docker official image) | `16.15-alpine3.24` | PostgreSQL License | БД |
| MinIO, сборка `pgsty/minio` | `RELEASE.2026-08-04T00-00-00Z` | AGPL-3.0 | S3 локально, официальные образы MinIO больше не публикуются |
| MinIO Client, сборка `pgsty/mc` | `RELEASE.2026-10-18T00-00-00Z` | AGPL-3.0 | создание бакетов в `minio-init` |
