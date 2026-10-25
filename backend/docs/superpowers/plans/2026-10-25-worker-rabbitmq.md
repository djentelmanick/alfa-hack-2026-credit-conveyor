# Worker и RabbitMQ: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** конвейер, reconciler и `ExpirationJob` переезжают в отдельный процесс `worker`; задачи и события идут через RabbitMQ.

**Architecture:** `api` коммитит заявку и кладёт `PipelineTask` в quorum-очередь через порт `TaskQueue`; `worker` потребляет её
(`PipelineConsumer` → идемпотентный `PipelineOrchestrator.run`). События обоих процессов публикуются в fanout
`applications.events`, `api` читает их `EventSubscriber` и отдаёт в WebSocket. Домен про RabbitMQ не знает.

**Tech Stack:** Python 3.15, FastAPI, SQLAlchemy 2 async, aio-pika, RabbitMQ 4.3, Alembic.

**Spec:** `docs/superpowers/specs/2026-10-25-worker-rabbitmq-design.md` (и `docs/BACKEND_SPEC.md` разд. 6, 10, 11;
`docs/API.md` 2.4, 2.6, 4.4).

## Global Constraints

- Новых тестов нет: тесты пишутся отдельным шагом по просьбе. Проверка каждой задачи: `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app`. Старые тесты на `TaskRunner`/`recover()` могут краснеть до шага тестов.
- Коммиты только по явной просьбе; задачи разделены так, чтобы каждую можно было закоммитить отдельно.
- `app/domain` не импортирует aio-pika, SQLAlchemy и ничего из `infra`/`application`.
- API aio-pika сверять через context7, не по памяти.
- Комментарии на русском и только «почему».
- RabbitMQ образ `rabbitmq:4.3.7-management-alpine`, пользователь `app`/`app`; `RABBITMQ_URL=amqp://app:app@rabbitmq:5672/`,
  `WORKER_PREFETCH=4`, `RECONCILE_INTERVAL_SEC=30`, `PIPELINE_STALE_AFTER_SEC=120`.
- Имена из BACKEND_SPEC: `TaskQueue`, `PipelineTask`, `PipelineLock`, `PgAdvisoryPipelineLock`, `RabbitTaskQueue`,
  `RabbitStatusPublisher`, `PipelineConsumer`, `EventSubscriber`, `PipelineReconciler`, `FindingsBuilder`, `CheckingData`.
- Тексты снимков: FAILED `{"error": "Нет ответа после {N} попыток"}`, SKIPPED `{"error": "Не запрашивали: нет ответа ЕГРЮЛ"}`,
  `attempts = 0` у SKIPPED; правило без данных: `outcome = NO_DATA`, `actual = "нет ответа"`.

## Review Focus

1. Worker стартует раньше, чем api применил миграции → `depends_on: api: service_healthy`, иначе падение на старте.
2. RabbitMQ недоступен в момент `submit` → заявка сохранена, `202`, ошибка в логе, reconciler подхватит через 120 с.
3. Публикация события после commit падает → вызывающий не падает (обёртка в `RabbitStatusPublisher`).
4. Сломан ЕГРЮЛ → нет `KeyError`/`AttributeError` в правилах и в `ReportBuilder` при пустых полях `CheckingData`.
5. Повторная доставка той же задачи, пока первая ещё идёт → вторая не захватывает lock и ack без работы.

Проверка этих пунктов — вручную в Task 8 (новых тестов нет по договорённости).

---

### Task 1: окружение и настройки

**Files:**
- Modify: `docker-compose.yml`, `.env.example`, `app/infra/config/settings.py`, `pyproject.toml`, `uv.lock`,
  `Dockerfile` (комментарий у CMD), `../.github/workflows/backend.yml`, `README.md` (адрес консоли RabbitMQ)

**Interfaces:**
- Produces: `Settings.rabbitmq_url: str`, `worker_prefetch: int = 4 (gt=0)`, `reconcile_interval_sec: int = 30 (gt=0)`,
  `pipeline_stale_after_sec: int = 120 (gt=0)`.

- [ ] **Step 1:** `uv add aio-pika` (последняя стабильная, проверить, что ставится на 3.15; если нет — остановиться и сообщить).
- [ ] **Step 2:** поля в `Settings` и `.env.example` (`RABBITMQ_URL=amqp://app:app@rabbitmq:5672/` и три числа, плюс
  закомментированный `TEST_RABBITMQ_URL=amqp://app:app@localhost:5672/`).
- [ ] **Step 3:** compose: сервис `rabbitmq` (env `RABBITMQ_DEFAULT_USER/PASS: app`, порты `5672`, `15672`, том
  `rabbitmq-data`, healthcheck `rabbitmq-diagnostics -q ping`); `api.depends_on.rabbitmq: service_healthy`; сервис `worker`
  (`build: .`, тот же `env_file`, `command: ["python", "-m", "app.worker"]`, `depends_on`: `api` healthy, `rabbitmq` healthy,
  без портов и healthcheck).
- [ ] **Step 4:** CI: сервис `rabbitmq` с теми же env и `TEST_RABBITMQ_URL` для шага тестов.
- [ ] **Step 5:** `docker compose config` проходит; ruff/mypy зелёные.

### Task 2: домен — порты, run_id, SKIPPED, CheckingData

**Files:**
- Modify: `app/domain/ports.py`, `app/domain/enums.py`, `app/domain/entities/application.py`,
  `app/domain/checks/context.py`, `app/domain/checks/stop_factors.py`, `app/domain/checks/__init__.py`

**Interfaces:**
- Produces:
  - `FetchStatus.SKIPPED = "SKIPPED"`.
  - `@dataclass(frozen) PipelineTask(application_id: UUID, run_id: UUID)` в `ports.py`.
  - `class TaskQueue(ABC): async def enqueue(self, task: PipelineTask) -> None` (бросает при недоступности брокера).
  - `class PipelineLock(ABC): def hold(self, application_id: UUID) -> AbstractAsyncContextManager[bool]`.
  - `ApplicationRepository.list_stale(statuses: Collection[ApplicationStatus], updated_before: datetime) -> list[Application]`.
  - `TaskRunner` удалён.
  - `Application.pipeline_run_id: UUID | None` (параметр конструктора, по умолчанию `None`); `submit(...)` и
    `start_recheck(...)` ставят `self.pipeline_run_id = uuid4()`.
  - `Application.missing_sources() -> list[SourceType]`: источники, у которых есть снимок FAILED или SKIPPED
    (заменяет `failed_sources`; `ensure_sources_ready` использует его).
  - `CheckingData` (frozen, kw_only): `application`, `company: CompanyProfile | None`, `credit_report: CreditReport | None`,
    `related_credit_reports: tuple[CreditReport, ...]`, `turnover`, `statement`, `tax: TaxInfo | None`,
    `bailiff: BailiffInfo | None`, `director_bailiff`, `cbr_level: KycLevel | None`, `rosfin_hit: bool | None`,
    `reported_annual_revenue`, `missing: frozenset[SourceType]`, `now: datetime`; свойство `today`.
  - `StopFactorRule.requires: ClassVar[frozenset[SourceType]]`; `check(data: CheckingData) -> CheckResult` даёт
    `NO_DATA`/`"нет ответа"`, если `requires & data.missing`; иначе `_evaluate(data)`. В `_evaluate` поля, нужные по
    `requires`, гарантированно не `None` — достать через хелпер, бросающий `ValueError`, без `assert`.
    `requires`: Inactive/Age/Okved `{EGRUL}`, Overdue `{EGRUL, BKI}`, TaxBlock `{FNS, FSSP}`, Cbr `{CBR_KYC}`,
    Rosfin `{ROSFIN}`, Disqualified `{FNS}`.

- [ ] **Step 1:** порты, enum, `PipelineTask`.
- [ ] **Step 2:** `Application`: поле, генерация run_id, `missing_sources`.
- [ ] **Step 3:** `CheckingData` и переписанные правила; экспорт в `checks/__init__.py`.
- [ ] **Step 4:** `uv run mypy app` — ожидаемо красный в `application/` и `infra/` до Task 3–4; домен без ошибок.

### Task 3: БД — миграция, репозиторий, lock, поломки моков

**Files:**
- Create: `alembic/versions/0007_pipeline_run_and_mock_failures.py`, `app/infra/db/pipeline_lock.py`
- Modify: `app/infra/db/models.py`, `app/infra/db/mappers.py`, `app/infra/db/repositories.py`,
  `app/infra/mocks/base.py`, `app/infra/mocks/__init__.py`, `app/api/v1/deps.py` (`MockFailureSwitch.set` → async),
  `app/api/v1/dev.py`

**Interfaces:**
- Consumes: Task 2 (`list_stale`, `pipeline_run_id`, `PipelineLock`).
- Produces:
  - колонка `applications.pipeline_run_id UUID NULL`; таблица `mock_failures(source VARCHAR(32), inn VARCHAR(12))`, PK по обоим.
  - `PgAdvisoryPipelineLock(engine: AsyncEngine)`: `hold` открывает своё соединение, `SELECT pg_try_advisory_lock(:key)`,
    по выходу `pg_advisory_unlock`. Ключ — первые 8 байт UUID как signed int64.
  - `MockFailures(session_factory)`: `async set(source, inn, *, enabled)`, `async failing_inns(source) -> frozenset[str]`,
    `async clear()`. `MockSourceAdapter.raise_if_broken` становится async и вызывается с `await` во всех моках.

- [ ] **Step 1:** миграция 0007 (revision `"0007"`, down `"0006"`), модель, маппер (`pipeline_run_id` в обе стороны).
- [ ] **Step 2:** `SqlApplicationRepository.list_stale`: `status IN (...) AND updated_at < :before`, старые сначала.
- [ ] **Step 3:** `PgAdvisoryPipelineLock`.
- [ ] **Step 4:** `MockFailures` на таблице (`INSERT ... ON CONFLICT DO NOTHING` / `DELETE`), адаптеры моков на `await`,
  `dev.py` на `await`.
- [ ] **Step 5:** ruff, mypy по `app/infra` без новых ошибок.

### Task 4: конвейер — сбор при сбоях, идемпотентный run, reconciler, сервисы

**Files:**
- Modify: `app/application/pipeline.py`, `app/application/application_service.py`, `app/application/analyst_service.py`,
  `app/application/report_builder.py` (если читает `failed_sources`)
- Create: `app/application/pipeline_reconciler.py`

**Interfaces:**
- Consumes: Task 2, Task 3.
- Produces:
  - `CollectedData(data: CheckingData, snapshots: tuple[SourceSnapshot, ...])`, `missing() -> frozenset[SourceType]`,
    `context() -> PipelineContext | None`.
  - `DataCollector.collect(app, on_step) -> CollectedData`: без ЕГРЮЛ продолжает по ИНН компании, `company_kind`
    берётся из `app.company_kind`; зависимые запросы → `SKIPPED`; шаг `ROSFIN` публикуется всегда.
    Сообщение FAILED по числу попыток из настройки.
  - `PipelineOrchestrator(..., lock: PipelineLock)`; `async run(application_id: UUID, run_id: UUID) -> None`;
    `checking(collected)`: стоп-факторы по `collected.data` всегда, строки `SOURCE_{source}` с `NO_DATA` по `missing`;
    если `missing` не пуст → `SOURCE_DOWN`, иначе прежняя логика STOP → NEEDS_INFO → OK. `recover()` удалён.
  - `PipelineReconciler(uow, queue: TaskQueue, clock, stale_after: timedelta)`, `async run_once() -> int`.
  - `ApplicationService(..., queue: TaskQueue, ...)` и `AnalystService(..., queue: TaskQueue, ...)` вместо `runner`/`pipeline`;
    после commit и publish: `try: await queue.enqueue(PipelineTask(app.id, app.pipeline_run_id))`, ошибка → `logger.exception`.
  - `AnalystService.report`: в `SUBMITTED`/`CHECKING` или без снимков → `InvalidStatus` (существующая доменная ошибка, 409).

- [ ] **Step 1:** `DataCollector` и `CollectedData`.
- [ ] **Step 2:** `PipelineOrchestrator.run` с lock и проверкой run_id; `checking`.
- [ ] **Step 3:** `PipelineReconciler`.
- [ ] **Step 4:** сервисы на `TaskQueue`; 409 у отчёта.
- [ ] **Step 5:** mypy по `app/application` чистый.

### Task 5: RabbitMQ-адаптеры

**Files:**
- Create: `app/infra/messaging/__init__.py`, `topology.py`, `codec.py`, `connection.py`, `task_queue.py`,
  `publisher.py`, `consumer.py`, `subscriber.py`
- Modify: `app/infra/realtime/ws_manager.py`, `app/infra/tasks.py`

**Interfaces:**
- Consumes: `PipelineTask`, `TaskQueue`, `StatusPublisher`, `ApplicationEvent`, `PipelineOrchestrator.run`.
- Produces:
  - `topology.py`: константы `PROCESS_QUEUE = "applications.process"`, `DLX = "applications.dlx"`,
    `DLQ = "applications.process.dlq"`, `EVENTS_EXCHANGE = "applications.events"`, `DELIVERY_LIMIT = 5`;
    `async declare(channel) -> Topology` (идемпотентно).
  - `codec.py`: `encode_task/decode_task` (бросает `MalformedMessage`), `encode_event/decode_event`
    (`type` = `status_changed` | `step`, поля событий, время ISO с `Z`).
  - `RabbitConnection(url)`: `async start()`, `async close()`, `channel(prefetch: int | None = None)`; robust-соединение.
  - `RabbitTaskQueue(connection)`: persistent, `content_type="application/json"`.
  - `RabbitStatusPublisher(connection)`: исключение публикации логируется, не пробрасывается.
  - `PipelineConsumer(connection, run: Callable[[UUID, UUID], Awaitable[None]], prefetch: int)`: `start()`, `stop()`;
    ack после `run`; `MalformedMessage` или `NotFound` → `reject(requeue=False)`; прочие исключения →
    `nack(requeue=True)`.
  - `EventSubscriber(connection, deliver: Callable[[ApplicationEvent], Awaitable[None]])`: exclusive auto-delete очередь
    на `EVENTS_EXCHANGE`; битое событие логируется и подтверждается.
  - `WsConnectionManager`: `publish` → `deliver`, наследование от `StatusPublisher` убрано.
  - `AsyncioTaskRunner`: остаются `every()` и `shutdown()`; `enqueue`/`drain` удаляются.

- [ ] **Step 1:** сверить через context7: `connect_robust`, `declare_queue(arguments=...)`, quorum `x-queue-type`,
  `message.process(...)` vs ручной ack/nack/reject, `set_qos(prefetch_count=...)`, `ExchangeType.FANOUT`.
- [ ] **Step 2:** topology, codec, connection.
- [ ] **Step 3:** task_queue, publisher.
- [ ] **Step 4:** consumer, subscriber; правка ws_manager и tasks.
- [ ] **Step 5:** ruff, mypy по `app/infra/messaging` чистые.

### Task 6: composition root и точка входа worker

**Files:**
- Modify: `app/main.py`, `app/api/v1/deps.py` (`Container`: убрать `expiration`), `Dockerfile` (комментарий у CMD)
- Create: `app/worker.py`

**Interfaces:**
- Consumes: всё выше.
- Produces:
  - `_build_pipeline(settings, uow, engine, publisher, clock, scoring, failures) -> PipelineOrchestrator` и
    `_build_sources(...)` — общие для api и worker.
  - `build_container(settings, engine, *, clock=None, scoring=None, connection: RabbitConnection | None = None) -> Container`;
    `lifecycle` api: `connection.start()` → `declare` → `EventSubscriber.start()`; stop в обратном порядке.
  - `@dataclass Worker`: `async start()` (connect, declare, `reconciler.run_once()`, `every(RECONCILE_INTERVAL_SEC, ...)`,
    `every(60, expiration.run_once)`, `consumer.start()`), `async stop()`.
  - `build_worker(settings, engine, *, clock=None, scoring=None) -> Worker`.
  - `app/worker.py`: `main()` — логирование, `create_engine`, `build_worker`, ожидание SIGTERM/SIGINT через
    `loop.add_signal_handler`, `stop()`, `engine.dispose()`; `if __name__ == "__main__": asyncio.run(main())`.

- [ ] **Step 1:** вынести общую сборку, переписать `build_container` и `BackgroundServices`.
- [ ] **Step 2:** `build_worker`, `Worker`, `app/worker.py`.
- [ ] **Step 3:** `uv run mypy app` и ruff полностью зелёные.

### Task 7: findings

**Files:**
- Create: `app/application/findings_builder.py`
- Modify: `app/application/application_service.py` (`findings(claims, application_id)`),
  `app/api/v1/schemas/application.py` (`ApplicationFindingsOut` и вложенные), `app/api/v1/applications.py`

**Interfaces:**
- Produces: `FindingsBuilder.build(app: Application) -> ApplicationFindings` (доменно-нейтральный dataclass в
  `findings_builder.py`); `POST /applications/findings`, тело `{application_id}`, ответ по `API.md` 4.4:
  `collected_at` = максимальный `fetched_at` снимков; `company` из OK-снимка EGRUL; `credit_load` из OK-снимка BKI
  с `subject_inn == app.inn`; `revenue.account_turnover` через `turnover_from_payload`; `revenue.annual_report` из OK-снимка
  GIR_BO с непустым `value`, `year = fetched_at.year - 1`. Отсутствующие разделы не выводятся (`response_model_exclude_none`).
  Ошибки: чужая → 403, нет → 404, `SUBMITTED`/`CHECKING`/нет снимков → 409 `INVALID_STATUS`.

- [ ] **Step 1:** builder и метод сервиса.
- [ ] **Step 2:** схема и роутер (без бизнес-логики).
- [ ] **Step 3:** ruff, mypy.

### Task 8: документация и ручная проверка

**Files:**
- Modify: `docs/BACKEND_SPEC.md` (раздел «Моки и синтетика»: поломки в таблице `mock_failures`; миграция 0007 где перечислены),
  `README.md` (сервисы, `docker compose logs -f worker`, консоль RabbitMQ http://localhost:15672 `app`/`app`)

- [ ] **Step 1:** правки документации.
- [ ] **Step 2:** `docker compose up --build -d`; `docker compose ps` — пять сервисов `running` (+ `minio-init` exited 0).
- [ ] **Step 3:** сценарий 1 по HTTP (логин `kolos`, создание, отправка) — в логах worker прогон, заявка в
  `MANUAL_REVIEW` с `SCORING_NOT_IMPLEMENTED`; в консоли RabbitMQ есть очереди `applications.process` и `.dlq`.
- [ ] **Step 4:** Review Focus: остановить `rabbitmq`, отправить заявку → 202 и ошибка в логе api; поднять — reconciler
  подхватил. Сломать EGRUL через `/dev/mocks/failures` → отчёт с FAILED и SKIPPED, статус `MANUAL_REVIEW`.
- [ ] **Step 5:** `uv run ruff check . && uv run ruff format --check . && uv run mypy app`; сообщить, какие старые тесты
  краснеют и почему.
