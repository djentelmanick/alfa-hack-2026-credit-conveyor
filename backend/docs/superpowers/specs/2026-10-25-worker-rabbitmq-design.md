# Этап 11: worker и RabbitMQ — дизайн

Дата: 2026-10-25. Источник требований: `docs/PHASES.md` этап 11, `docs/BACKEND_SPEC.md` разделы 1, 2, 6, 10, 11, 13,
`docs/API.md` разделы 2.4, 2.6, 4.4, 4.5. Здесь только то, как это ложится на текущий код, и решения сверх спецификации.

## Цель

`docker compose up` поднимает `api`, `worker`, `rabbitmq`, `postgres`, `minio`. `api` принимает запросы и держит
WebSocket, конвейер (CHECKING и дальше), reconciler и `ExpirationJob` работают в `worker`. Задачи и события идут через
RabbitMQ. Бизнес-правила SCOPE и существующие контракты API не меняются, кроме нового `POST /applications/findings`
и `409` у отчёта до конца сбора.

## Порядок работы

1. Реализация по шагам ниже, проверки: `ruff check`, `ruff format --check`, `mypy app`, ручной прогон сценария 1
   через `docker compose`.
2. Тесты (адаптация существующих и новые по критериям готовности этапа 11) — отдельным шагом по просьбе.
   До этого `pytest` может быть красным там, где тесты опираются на `TaskRunner` и `recover()`.
3. Коммиты только по просьбе; шаги разделены так, чтобы их можно было закоммитить по одному.

## Шаг 1. Окружение

- `docker-compose.yml`: `rabbitmq` (`rabbitmq:4.3.7-management-alpine`, `RABBITMQ_DEFAULT_USER/PASS=app`,
  порты 5672 и 15672, healthcheck `rabbitmq-diagnostics -q ping`), `worker` (тот же образ, `python -m app.worker`,
  `depends_on`: `api` healthy, `rabbitmq` healthy). `api` зависит от `rabbitmq` healthy.
- `Settings` и `.env.example`: `RABBITMQ_URL`, `WORKER_PREFETCH=4`, `RECONCILE_INTERVAL_SEC=30`,
  `PIPELINE_STALE_AFTER_SEC=120`.
- `pyproject.toml` / `uv.lock`: `aio-pika`. CI: сервис RabbitMQ и `TEST_RABBITMQ_URL`.

## Шаг 2. Очередь, идемпотентность, сбор при сбоях

Домен:
- `PipelineTask(application_id, run_id)`; порты `TaskQueue.enqueue`, `PipelineLock.hold` (async context → bool).
  `TaskRunner` удаляется из портов.
- `Application.pipeline_run_id: UUID | None`, новый на переходе в `SUBMITTED` и на `recheck` (в `CHECKING`).
- `ApplicationRepository.list_stale(statuses, updated_before)`.
- `FetchStatus.SKIPPED`.
- `CheckingData` в `checks/context.py`: поля источников необязательные, `missing: frozenset[SourceType]`.
  `StopFactorRule.requires`; правило без нужного источника даёт `NO_DATA` с `actual = "нет ответа"`.
  Правила проверяют `CheckingData`, `PipelineContext` получают проверка выписки, скоринг, триггеры и решение.

Приложение:
- `DataCollector.collect`: при сбое ЕГРЮЛ опрашиваются источники по ИНН компании (БКИ компании, обороты, ФНС,
  ГИР БО по `Application.company_kind`, ФССП компании, ЦБ); БКИ и ФССП директора и учредителей и Росфинмониторинг
  получают снимок `SKIPPED` (`attempts = 0`, `{"error": "Не запрашивали: нет ответа ЕГРЮЛ"}`). Шаг `ROSFIN`
  публикуется всегда. `FAILED`: `{"error": "Нет ответа после N попыток"}`.
- `CollectedData`: `data: CheckingData`, `snapshots`, `missing()`, `context()` (только если ничего не пропало).
- `PipelineOrchestrator.run(application_id, run_id)`: lock → статус в `SUBMITTED/CHECKING/SCORING` и совпадение
  `run_id`, иначе выход без работы → прогон с текущего статуса. Стоп-факторы считаются всегда; если есть `missing`,
  итог `SOURCE_DOWN` (MANUAL_REVIEW, `SOURCE_UNAVAILABLE`) даже при сработавшем стоп-факторе.
- `PipelineReconciler.run_once()` (`application/pipeline_reconciler.py`) вместо `recover()`.
- `ApplicationService.submit` и `AnalystService.recheck`: commit → publish → `enqueue`; ошибка постановки
  только логируется.

Инфраструктура:
- `infra/messaging/`: `topology.py` (quorum `applications.process` с `x-delivery-limit=5` и DLX `applications.dlx`,
  quorum `applications.process.dlq`, fanout `applications.events`), `codec.py`, `RabbitConnection` (общее
  robust-соединение), `RabbitTaskQueue`, `PipelineConsumer`.
- `PipelineConsumer`: `prefetch = WORKER_PREFETCH`, ack после `run`; битое тело или нет заявки →
  `reject(requeue=False)`; прочие ошибки → `nack(requeue=True)`.
- `infra/db/pipeline_lock.py`: `PgAdvisoryPipelineLock` на отдельном соединении `pg_try_advisory_lock`.
- `infra/tasks.py`: остаётся только периодический `every()`.
- Миграция `0007`: `applications.pipeline_run_id`, значение `SKIPPED` (если статус снимка хранится enum-типом),
  таблица `mock_failures`.

## Шаг 3. События

- `RabbitStatusPublisher` в обоих процессах; ошибка публикации логируется, не бросается.
- `EventSubscriber` в lifespan `api`: exclusive auto-delete очередь, привязанная к `applications.events`,
  каждое событие → `WsConnectionManager.deliver`. `WsConnectionManager` больше не реализует `StatusPublisher`.

## Шаг 4. Отчёт после сбора

`AnalystService.report`: `409 INVALID_STATUS` в `SUBMITTED` и `CHECKING` или без снимков.

## Шаг 5. Findings

`FindingsBuilder` (`application/findings_builder.py`) строит `ApplicationFindings` из снимков в PostgreSQL;
`POST /applications/findings` (К) по схеме `API.md` 4.4: `403` чужая, `404`, `409` до конца сбора.

## Решение сверх спецификации: поломки моков в PostgreSQL

`MockFailures` в памяти процесса не видна worker. Реестр поломок переезжает в таблицу `mock_failures(source, inn)`:
`/dev/mocks/failures` пишет в неё из `api`, моки обоих процессов читают при каждом запросе. Класс остаётся в
`app/infra/mocks`, его методы становятся асинхронными; домен не меняется. Описание добавляется в
`docs/BACKEND_SPEC.md` (раздел «Моки и синтетика»).

## Composition root

`main.py`: общий хелпер собирает источники и `PipelineOrchestrator`. `build_container` — api без конвейера
и фоновых задач, с `RabbitTaskQueue`, `RabbitStatusPublisher`, `EventSubscriber`. `build_worker` — `PipelineConsumer`,
`PipelineReconciler`, `ExpirationJob`, `RabbitStatusPublisher`. `app/worker.py` — точка входа, остановка по SIGTERM/SIGINT.

## Вне рамок

Outbox, несколько worker в compose, перенос этапа 1 в worker, новые тесты до отдельной просьбы.
