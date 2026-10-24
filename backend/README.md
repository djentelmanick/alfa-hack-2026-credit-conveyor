# Бэкенд кредитного конвейера

FastAPI-бэкенд «Умного кредитного кабинета предпринимателя». Архитектура описана в `docs/BACKEND_SPEC.md`,
контракт с фронтендом в `docs/API.md`, этапы работы в `docs/PHASES.md`.

## Запуск

```bash
docker compose up --build                                # api, postgres, minio, minio-init
docker compose exec api alembic upgrade head             # миграции
curl http://localhost:8000/api/v1/health                 # {"status":"ok","db":"ok","s3":"ok"}
```

| Адрес | Что там |
|---|---|
| http://localhost:8000/docs | Swagger |
| http://localhost:9001 | консоль MinIO (`minioadmin` / `minioadmin`) |
| localhost:5432 | PostgreSQL (`app` / `app`, БД `credit`) |

Compose берёт переменные из `.env.example`. Чтобы поменять значения, создайте рядом `.env`:
он переопределит `.env.example` и в git не попадёт.

MinIO запускается из образов `pgsty/minio` и `pgsty/mc`: официальные образы MinIO больше не публикуются.

## Разработка

Нужен [uv](https://docs.astral.sh/uv/) и Python 3.15 (`uv python install 3.15`).

```bash
uv sync                       # зависимости из uv.lock
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

Новая зависимость: `uv add <пакет>` (или `uv add --dev`), затем строка в `THIRD_PARTY_NOTICES.md`.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | обязательная | подключение к БД, `postgresql+asyncpg://...` |
| `JWT_SECRET` | обязательная, от 32 символов | подпись JWT |
| `JWT_TTL_MINUTES` | `60` | время жизни токена |
| `S3_ENDPOINT` | обязательная | адрес S3 изнутри docker-сети |
| `S3_PUBLIC_ENDPOINT` | обязательная | адрес S3 для presigned-ссылок, видимый из браузера |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | обязательные | доступ к S3, они же root-учётка MinIO в compose |
| `S3_REGION` | `us-east-1` | регион S3 |
| `S3_BUCKET_FILES` | `credit-files` | бакет выписок |
| `S3_BUCKET_CONTRACTS` | `credit-contracts` | бакет договоров |
| `PRESIGNED_TTL_SECONDS` | `300` | время жизни ссылки на файл |
| `MAX_UPLOAD_MB` | `10` | лимит выписки |
| `PIPELINE_STEP_DELAY_MS` | `800` | пауза на шаге конвейера |
| `SOURCE_RETRY_ATTEMPTS` | `3` | число попыток обращения к источнику |
| `SOURCE_RETRY_PAUSE_MS` | `500` | пауза между попытками |
| `OFFER_TTL_DAYS` | `7` | срок одобрения и контрпредложения |
| `DEMO_MODE` | `false` (в `.env.example` `true`) | включает `/dev/*` и эмуляцию |
| `CORS_ORIGINS` | пусто | адреса фронтенда через запятую |
