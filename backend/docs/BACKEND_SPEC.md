# BACKEND_SPEC — архитектура бэкенда

Спецификация бэкенда «Умного кредитного кабинета предпринимателя».
Бизнес-правила, числа и сценарии взяты из `docs/SCOPE.md` (дальше «SCOPE, раздел N»).
Контракт с фронтендом описан в `docs/API.md`. Порядок реализации описан в `docs/PHASES.md`.

Содержание:

1. Стек и окружение
2. Слои и структура пакетов
3. ООП и паттерны
4. Доменная модель
5. Статусы заявки
6. Конвейер: проверки
7. Скоринг
8. Решение
9. Продукты
10. Сервисы приложения, порты, адаптеры
11. Асинхронная обработка и события
12. Аутентификация (JWT)
13. WebSocket
14. Файлы в S3
15. Договор, операции, эмуляция
16. История и аудит
17. Последовательности
18. Ошибки
19. Решения сверх SCOPE и что исключено

---

## 1. Стек и окружение

| Слой | Выбор |
|---|---|
| Язык | Python 3.15 |
| Веб | FastAPI, Pydantic v2, uvicorn, python-multipart (загрузка выписки) |
| БД | PostgreSQL 16, SQLAlchemy 2 async + asyncpg, Alembic |
| Файлы | S3-совместимое хранилище, локально MinIO, клиент aioboto3 |
| Очередь и события | RabbitMQ 4.3, клиент aio-pika |
| Авторизация | JWT HS256 (PyJWT), пароли argon2 |
| PDF договора | reportlab (BSD), шрифт DejaVu Sans в `app/infra/documents/fonts/` для кириллицы; выбран на этапе 8 |
| Тесты и качество | pytest, pytest-asyncio, httpx, ruff, mypy strict |
| Запуск | Docker Compose |

### Контейнеры (C4, уровень 2)

```mermaid
flowchart LR
    analyst(["Аналитик"]) --> web
    client(["Клиент"]) --> web
    subgraph compose["Умный кредитный кабинет (docker compose)"]
        web["Веб-приложение<br/>React, кабинеты клиента и аналитика"]
        api["Backend API<br/>Python 3.15, FastAPI<br/>принимает запросы, отдаёт ответы и WebSocket"]
        worker["Worker<br/>Python 3.15, app.worker<br/>собирает данные и делает проверки"]
        mq[("RabbitMQ<br/>очередь задач и события")]
        pg[("PostgreSQL<br/>заявки, собранные данные, решения")]
        s3[("S3 / MinIO<br/>выписки 1С, PDF договоров")]
    end
    ext["Внешние источники<br/>ЕГРЮЛ, БКИ, ФНС, ФССП, ЦБ, РФМ, системы банка<br/>(сейчас моки)"]
    web -->|"1. REST, JWT"| api
    api -->|"2. сохраняет заявку"| pg
    api -->|"3. задача в очередь"| mq
    mq -->|"4. доставляет задачу"| worker
    worker -.->|"5. запросы"| ext
    worker -->|"6. сохраняет собранное"| pg
    worker -->|"читает выписку"| s3
    worker -->|"7. события статусов"| mq
    mq -->|"8. события"| api
    api -->|"9. WebSocket"| web
    api -->|"файлы и ссылки"| s3
```

Как проходит заявка:

1. Клиент отправляет заявку. `api` проверяет JWT и форму (этап 1), сохраняет заявку в PostgreSQL со статусом
   `SUBMITTED`, кладёт задачу в очередь `applications.process` и сразу отвечает `202`.
2. `worker` берёт задачу, ставит `CHECKING` и по очереди ходит во все источники этапа 2.
3. Ответы источников сохраняются в PostgreSQL. После каждого шага `worker` публикует событие в обмен `applications.events`.
4. `api` получает события из RabbitMQ и пересылает их в WebSocket: клиент видит прогресс.
5. Когда всё собрано и проверено, заявка получает итоговый статус; нестандартная попадает в очередь аналитика.

Стрелка «читает выписку» зарезервирована: сейчас выписку разбирает и проверяет ЭП `api` при загрузке (этап 1,
синхронный ответ `StatementOut.signature_status`), операции лежат в PostgreSQL, и worker в S3 не ходит.

Граница между процессами: `api` обращается к источникам только там, где нужен синхронный ответ по контракту
(`/companies/get`, `/consents/give`, `/applications/create`, `FormValidator`, проверка паспорта при регистрации,
ЭП выписки при загрузке). Весь этап 2 (CHECKING) со всеми его источниками выполняет только `worker`.

Аналитик видит полный отчёт: все ответы источников, сравнения, стоп-факторы. Клиент видит только что нашлось
(`/applications/findings`): данные компании, кредитную нагрузку, выручку, без внутренних сравнений банка.

### Docker Compose

| Сервис | Назначение |
|---|---|
| `api` | FastAPI-приложение: REST, этап 1 (форма), постановка задач, WebSocket. Конвейер и фоновые задачи не выполняет. Перед стартом применяет миграции |
| `worker` | тот же образ, процесс `python -m app.worker`: конвейер (этапы 2 и 3), reconciler, `ExpirationJob`. Стартует после того, как `api` стал healthy (миграции применены) |
| `rabbitmq` | брокер `rabbitmq:4.3.7-management-alpine`: очередь задач и обмен событий (раздел 11). Пользователь задаётся через `RABBITMQ_DEFAULT_USER` и `RABBITMQ_DEFAULT_PASS`, потому что `guest` пускают только с localhost |
| `postgres` | основная БД |
| `minio` | S3 для выписок и договоров. Официальные образы MinIO больше не публикуются, используется сборка `pgsty/minio` (и `pgsty/mc` для `minio-init`) |
| `minio-init` | одноразовый контейнер, создаёт бакеты `credit-files` и `credit-contracts` |

`docker compose up` поднимает всё (требование кейса, SCOPE, раздел 10).

### Переменные окружения

| Переменная | Пример | Назначение |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://app:app@postgres:5432/credit` | подключение к БД |
| `JWT_SECRET` | случайная строка от 32 байт | подпись JWT |
| `JWT_TTL_MINUTES` | `60` | время жизни токена |
| `S3_ENDPOINT` | `http://minio:9000` | адрес S3 |
| `S3_PUBLIC_ENDPOINT` | `http://localhost:9000` | адрес S3 для presigned-ссылок, видимый из браузера |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | | доступ к S3 |
| `S3_BUCKET_FILES` | `credit-files` | бакет выписок |
| `S3_BUCKET_CONTRACTS` | `credit-contracts` | бакет договоров |
| `RABBITMQ_URL` | `amqp://app:app@rabbitmq:5672/` | подключение к RabbitMQ (api и worker) |
| `WORKER_PREFETCH` | `4` | сколько задач один worker обрабатывает одновременно |
| `RECONCILE_INTERVAL_SEC` | `30` | как часто reconciler ищет зависшие заявки |
| `PIPELINE_STALE_AFTER_SEC` | `120` | через сколько секунд без смены статуса заявка в конвейере считается зависшей |
| `PRESIGNED_TTL_SECONDS` | `300` | время жизни ссылки на файл |
| `MAX_UPLOAD_MB` | `10` | лимит выписки |
| `PIPELINE_STEP_DELAY_MS` | `800` | пауза на шаге конвейера, имитирует внешние сервисы (SCOPE, раздел 8) |
| `SOURCE_RETRY_ATTEMPTS`, `SOURCE_RETRY_PAUSE_MS` | `3`, `500` | ретраи моков (SCOPE, раздел 4) |
| `OFFER_TTL_DAYS` | `7` | срок одобрения и контрпредложения |
| `DEMO_MODE` | `true` | включает `/dev/*` и кнопки эмуляции |
| `CORS_ORIGINS` | `http://localhost:5173` | адрес фронтенда |

---

## 2. Слои и структура пакетов

```mermaid
flowchart TB
    API["api/v1<br/>роутеры, Pydantic DTO, CurrentUser, обработчики ошибок, WebSocket"]
    APP["application<br/>сценарии: ApplicationService, PipelineOrchestrator, AnalystService,<br/>AuthService, SigningService, ContractService, EmulationService"]
    DOM["domain<br/>сущности, ApplicationStateMachine, проверки, скоринг (заглушка), решение,<br/>LoanProduct, порты"]
    INF["infra<br/>SQLAlchemy, моки источников, RetryingGateway, S3, JWT, RabbitMQ (aio-pika),<br/>WsConnectionManager, парсер 1С, периодические задачи, products.yaml"]
    API -->|вызывает| APP
    APP -->|управляет| DOM
    INF -.->|реализует порты| DOM
```

Правила:

- Роутер принимает DTO из тела POST, получает `TokenClaims` из `Depends(current_user)`, вызывает сервис, возвращает DTO.
- Пользователь и роль никогда не приходят в теле запроса, только из токена.
- Сервис открывает `UnitOfWork`, загружает агрегат, вызывает методы домена, коммитит, затем публикует события.
- Домен не импортирует FastAPI, SQLAlchemy, aioboto3, PyJWT и моки.
- Реализации подставляются в порты в `app/main.py` (composition root): `build_container` для `api`,
  `build_worker` для `worker`. `app/worker.py` только точка входа процесса.
- Домен и сервисы не знают про RabbitMQ: очередь задач и события доступны только через порты `TaskQueue`
  и `StatusPublisher`, реализации на aio-pika лежат в `app/infra/messaging`.

```text
app/
  main.py                    # FastAPI, DI, роутеры, обработчики ошибок; build_container и build_worker
  worker.py                  # точка входа `python -m app.worker`: потребитель очереди и периодические задачи
  api/v1/
    auth.py  catalog.py  companies.py
    applications.py  analyst.py
    contracts.py  emulation.py  dev.py
    ws.py                    # WebSocket /ws/applications
    schemas/                 # Pydantic DTO, тела всех POST
    deps.py                  # current_user из JWT, require_role, get_uow, сервисы
  application/
    auth_service.py  consent_service.py  company_service.py
    application_service.py  form_validator.py
    pipeline.py              # PipelineOrchestrator, DataCollector
    pipeline_reconciler.py   # PipelineReconciler: зависшие заявки снова в очередь
    analyst_service.py  report_builder.py  findings_builder.py
    signing_service.py  contract_service.py  emulation_service.py
  domain/
    entities/                # Application, Contract и их части
    value_objects.py         # Money, Period, Collateral, CompanyProfile...
    enums.py  errors.py  events.py
    state_machine.py
    checks/                  # CheckingData, PipelineContext, StopFactorRule, ManualReviewTrigger, StatementVerifier
    scoring/                 # ScoringModel, ScoringEngine (заглушка), ScoringResult
    decision/                # DecisionEngine, DecisionRule, RatePolicy
    products/                # LoanProduct, LumpSumLoan, RevolvingCreditLine, AnnuityCalculator, ProductCatalog
    ports.py                 # порты источников, репозиториев, UoW, Clock, StatusPublisher, TaskQueue, PipelineLock, FileStoragePort
  infra/
    db/                      # models.py, mappers.py, repositories.py, uow.py (SqlUnitOfWork)
    mocks/                   # адаптеры источников, data/ с синтетикой, seed.py
    parsers/statement_1c.py
    storage/s3.py            # S3FileStorage
    documents/contract_pdf.py  # ReportlabContractRenderer
    security/jwt.py          # JwtService
    security/passwords.py    # Argon2PasswordHasher
    clock.py                 # SystemClock
    db/pipeline_lock.py      # PgAdvisoryPipelineLock (pg_try_advisory_lock)
    messaging/               # topology.py, codec.py, RabbitTaskQueue, RabbitStatusPublisher,
                             # PipelineConsumer (worker), EventSubscriber (api)
    realtime/ws_manager.py   # WsConnectionManager: получает события от EventSubscriber
    tasks.py                 # AsyncioTaskRunner: периодические задачи worker
    config/products.yaml  config/catalog.py   # load_catalog
tests/
  unit/  integration/  e2e/
alembic/  Dockerfile (python:3.15-slim)  docker-compose.yml  .env.example
```

---

## 3. ООП и паттерны

| Принцип или паттерн | Где применяется |
|---|---|
| Инкапсуляция | статус меняется только через `Application.transition_to()`. Инварианты (покрытие залогом, срок предложения, лимит ВКЛ) проверяет сама сущность |
| Наследование | абстрактные базовые классы `StopFactorRule`, `ScoringModel`, `ManualReviewTrigger`, `DecisionRule`, `Contract`, `LoanProduct`, `MockSourceAdapter` |
| Полиморфизм | `LumpSumLoan` и `RevolvingCreditLine` по-разному считают платёж, открывают договор и разрешают операции. Конвейер работает с `LoanProduct` |
| Абстракция и DIP | домен объявляет порты, инфраструктура даёт реализации. Замена мока на реальный API не трогает домен |
| Открытость для расширения | новый продукт это запись в `products.yaml`, новое правило это новый класс в реестре, модель скоринга подключается реализацией `ScoringModel` |
| Strategy | способы проверки подлинности выписки, сменная модель скоринга (`ScoringModel`) |
| Chain of Responsibility | правила решения по порядку |
| State | `ApplicationStateMachine` |
| Repository и Unit of Work | доступ к БД |
| Decorator | `RetryingGateway` вокруг портов источников |
| Observer | `StatusPublisher` и подписчики WebSocket |
| Factory | `ProductCatalog` собирает `LoanProduct` из конфига |

---

## 4. Доменная модель

`Application` и `Contract` это агрегаты: всё внутри меняется только через их методы и сохраняется одной транзакцией.

```mermaid
classDiagram
direction TB
class User {
  +id: UUID
  +login: str
  +role: Role
  +full_name: str
  +passport_verified: bool
  +companies: list~UserCompany~
  +link_company(company)
}
class UserCompany {
  +inn: str
  +name: str
  +kind: CompanyKind
  +is_director: bool
}
class Application {
  +id: UUID
  +applicant_id: UUID
  +inn: str
  +company_kind: CompanyKind
  +company_name: str
  +okved: str
  +purpose: Purpose
  +product_code: str
  +variant: Variant
  +amount: Money
  +term_months: int
  +owner_guarantee: bool
  +status: ApplicationStatus
  +expires_at: datetime
  +accepted_offer_id: UUID
  +signing_code: SigningCode
  +pipeline_run_id: UUID
  +create_draft(form, product, now)$
  +update_form(form, product, now)
  +submit(product, actor, now, run_id)
  +transition_to(status, actor, reason, now, offer_ttl)
  +request_info(items, message, actor, now)
  +accept_offer(option_id, actor, now)
  +cancel(actor, reason, now)
  +is_active() bool
  +pull_events() list~StatusChangedEvent~
}
class CompanyProfile {
  +inn: str
  +kind: CompanyKind
  +name: str
  +ogrn: str
  +registered_at: date
  +status: CompanyStatus
  +okved: str
  +director: str
  +director_inn: str
  +address: str
  +director_changed_at: date
  +unreliable_records: list~str~
  +founders: list~Founder~
  +is_director(user) bool
  +age_months(today) int
}
class Collateral {
  +type: CollateralType
  +description: str
  +estimated_value: Money
  +covers(amount) bool
}
class Consent {
  +type: ConsentType
  +given_by: UUID
  +given_at: datetime
}
class BankStatement {
  +bank_name: str
  +account_inn: str
  +period_from: date
  +period_to: date
  +signature_status: SignatureStatus
  +file_key: str
  +months_covered() int
  +is_fresh(today) bool
  +annual_revenue() Money
}
class StatementOperation {
  +date: date
  +amount: Money
  +direction: Direction
  +counterparty_inn: str
}
class SourceSnapshot {
  +source: SourceType
  +status: FetchStatus
  +attempts: int
  +payload: dict
  +fetched_at: datetime
}
class CheckResult {
  +code: str
  +stage: CheckStage
  +expected: str
  +actual: str
  +outcome: CheckOutcome
}
class ScoringResult {
  +total: int
  +risk_class: RiskClass
  +debt_load: float
}
class FactorScore {
  +code: str
  +label: str
  +value: str
  +points: int
}
class Decision {
  +rule_code: str
  +outcome: DecisionOutcome
  +rate: Decimal
  +reasons: list~str~
  +decided_by: UUID
  +comment: str
}
class OfferOption {
  +id: UUID
  +product_code: str
  +variant: Variant
  +amount: Money
  +term_months: int
  +rate: Decimal
}
class InfoRequest {
  +items: list~str~
  +message: str
  +created_by: UUID
  +resolved_at: datetime
}
class StatusTransition {
  +from_status: ApplicationStatus
  +to_status: ApplicationStatus
  +actor_id: UUID
  +reason: str
  +at: datetime
}
class Contract {
  <<abstract>>
  +number: str
  +product_code: str
  +variant: Variant
  +rate: Decimal
  +term_months: int
  +status: ContractStatus
  +contract_date: date
  +apply(operation)
  +add_version(changes, author)
  +advance_month()
  +close()
}
class LoanContract {
  +principal: Money
  +outstanding: Money
  +prepay(amount)
}
class CreditLineContract {
  +limit: Money
  +available: Money
  +draw(amount) Tranche
  +repay_tranche(id, amount)
}
class Tranche {
  +amount: Money
  +issued_at: date
  +due_at: date
  +outstanding: Money
}
class ContractVersion {
  +version_no: int
  +document_key: str
  +changes: list~ContractChange~
  +reason: str
  +author_id: UUID
  +created_at: datetime
}
class ScheduleItem {
  +due_date: date
  +principal: Money
  +interest: Money
  +balance: Money
}
class Operation {
  +type: OperationType
  +amount: Money
  +contract_date: date
  +actor_id: UUID
  +balance_after: Money
}
User "1" --> "*" Application : подаёт
User "1" *-- "*" UserCompany
Application ..> CompanyProfile : по ИНН
Application "1" *-- "0..1" Collateral
Application "1" *-- "*" Consent
Application "1" *-- "*" BankStatement
BankStatement "1" *-- "*" StatementOperation
Application "1" *-- "*" SourceSnapshot
Application "1" *-- "*" CheckResult
Application "1" *-- "0..1" ScoringResult
ScoringResult "1" *-- "*" FactorScore
Application "1" *-- "*" Decision
Decision "1" *-- "*" OfferOption
Application "1" *-- "*" InfoRequest
Application "1" *-- "*" StatusTransition
Application "1" --> "0..1" Contract : выдача
Contract <|-- LoanContract
Contract <|-- CreditLineContract
CreditLineContract "1" *-- "*" Tranche
Contract "1" *-- "*" ContractVersion
ContractVersion "1" *-- "*" ScheduleItem
Contract "1" *-- "*" Operation
```

### Инварианты агрегата Application

- Создание только для пользователя, который руководитель компании по ЕГРЮЛ или сам ИП (SCOPE, раздел 3).
- Пока по ИНН есть активная заявка, новую создать нельзя.
- Для варианта с залогом `collateral.estimated_value >= amount * 0.5` (SCOPE, раздел 2).
- Для ООО без залога обязательно `owner_guarantee = true`. Для ИП поле не требуется.
- Правка полей анкеты разрешена только в `DRAFT` и `NEEDS_INFO`.
- `expires_at` выставляется при переходе в `APPROVED` и `COUNTER_OFFER` на `OFFER_TTL_DAYS` и сбрасывается при выходе из них.
- Сущность не держит порт `Clock`: текущее время (`now`) и срок предложения (`offer_ttl`) сервис передаёт в методы аргументами.
- Проверки, которым нужен продукт (покрытие залогом), получают `LoanProduct` аргументом. Процент покрытия берётся из
  `collateral_coverage_min_pct` варианта. В черновике залог можно не указывать (например, после выбора контрпредложения
  с залогом), но указанный залог обязан покрывать сумму. Полный набор (залог для `SECURED`, поручительство для ООО) проверяется в `submit`.
- `company_kind` сохраняется при создании (из ЕГРЮЛ/ЕГРИП): от него зависит правило поручительства.
  Там же запоминаются `company_name` и `okved`: карточка заявки (`ApplicationOut.company`) не ждёт ответа реестра.
- Согласия копируются в заявку при создании и обновляются перед отправкой (`replace_consents`): клиент может
  дать согласие уже после создания черновика.
- Результаты конвейера пишутся методами `record_snapshots`, `record_checks(stage, checks)`, `record_scoring`,
  `add_decision`. Повторный прогон CHECKING заменяет снимки и проверки этапа целиком.
- `pipeline_run_id` это id текущего прогона конвейера. Его задают `submit(..., run_id)` и `start_recheck(..., run_id)`,
  новый id генерирует сервис. Тот же id идёт в задачу очереди: worker выполняет задачу, только если id совпадает
  (раздел 11). У черновика поле пустое.
- Каждый переход статуса добавляет `StatusTransition` и событие `StatusChangedEvent`
  во внутренний список событий. Сервис забирает события через `pull_events()` после `commit`.

### Пользователь и согласия

- `User.companies` это компании в кабинете пользователя (таблица `user_companies`). Название и форма запоминаются
  при привязке, чтобы вход и `/auth/me` не зависели от ЕГРЮЛ. Компанию привязывают `seed.py` и `/consents/give`.
  Порта «найти компании по ФИО» нет: реестр ищет только по ИНН.
- Согласия даются по ИНН до заявки (`/consents/give`), поэтому хранятся отдельно от агрегата `Application`:
  `ConsentRepository`, ключ «ИНН, кто дал, тип». При создании заявки (этап 5) они копируются в `Application.consents`.
- Паспорт проверяется при регистрации и не хранится: дальше нужен только `passport_verified`.

### Перечисления

| Тип | Значения |
|---|---|
| `Role` | `CLIENT`, `ANALYST` |
| `CompanyKind` | `LLC` (ООО), `IE` (ИП) |
| `CompanyStatus` | `ACTIVE`, `LIQUIDATED`, `BANKRUPT`, `REORGANIZING` |
| `ProductCode` | `LUMP_SUM` (кредит одной суммой), `REVOLVING_LINE` (ВКЛ) |
| `Variant` | `UNSECURED`, `SECURED` |
| `Purpose` | `ONE_TIME_PURCHASE`, `DEVELOPMENT`, `REFINANCING`, `REGULAR_PURCHASES`, `SEASONAL_GAP` |
| `CollateralType` | `REAL_ESTATE`, `VEHICLE`, `EQUIPMENT`, `GOODS` |
| `ConsentType` | `BKI_COMPANY`, `BKI_PERSONAL`, `PERSONAL_DATA` |
| `ApplicationStatus` | `DRAFT`, `SUBMITTED`, `CHECKING`, `NEEDS_INFO`, `SCORING`, `MANUAL_REVIEW`, `APPROVED`, `COUNTER_OFFER`, `SIGNING`, `ISSUED`, `REJECTED`, `CANCELLED` |
| `RiskClass` | `A`, `B`, `C`, `D` |
| `DecisionOutcome` | `APPROVED`, `COUNTER_OFFER`, `NEEDS_INFO`, `MANUAL_REVIEW`, `REJECTED` |
| `SignatureStatus` | `VALID`, `INVALID`, `ABSENT` |
| `CheckStage` | `FORM`, `CHECKING`, `POST_SCORING` |
| `CheckOutcome` | `PASSED`, `FAILED`, `STOP_FACTOR`, `NO_DATA` |
| `SourceType` | `EGRUL`, `BKI`, `TURNOVER`, `FNS`, `GIR_BO`, `FSSP`, `CBR_KYC`, `ROSFIN`, `BANK_SIGNATURE` |
| `FetchStatus` | `OK`, `FAILED` (нет ответа после всех попыток), `SKIPPED` (не запрашивали: запросу нужны данные ЕГРЮЛ, а ЕГРЮЛ не ответил) |
| `ContractStatus` | `ACTIVE`, `CLOSED` |
| `OperationType` | `DISBURSEMENT`, `SCHEDULED_PAYMENT`, `PARTIAL_PREPAYMENT`, `FULL_REPAYMENT`, `LIMIT_OPENED`, `TRANCHE_DRAWN`, `INTEREST_ACCRUED`, `TRANCHE_REPAID`, `CONTRACT_EDITED` |

### Value objects

- `Money`: обёртка над `Decimal`, 2 знака, округление `ROUND_HALF_UP`, без смешивания с `float`.
- `Period`: `date_from`, `date_to`, `months()`.
- `Collateral`, `CompanyProfile`, `Founder`, `CreditReport`, `MonthlyTurnover`, `TaxInfo`, `BailiffInfo`: неизменяемые (`frozen`).

---

## 5. Статусы заявки

```mermaid
flowchart LR
DRAFT([DRAFT])
SUBMITTED([SUBMITTED])
CHECKING([CHECKING])
SCORING([SCORING])
NEEDS_INFO([NEEDS_INFO])
MANUAL_REVIEW([MANUAL_REVIEW])
APPROVED([APPROVED])
COUNTER_OFFER([COUNTER_OFFER])
SIGNING([SIGNING])
ISSUED([ISSUED])
REJECTED([REJECTED])
CANCELLED([CANCELLED])
DRAFT -->|POST submit| SUBMITTED
DRAFT -->|POST cancel| CANCELLED
SUBMITTED --> CHECKING
CHECKING -->|стоп-фактор| REJECTED
CHECKING -->|не хватает данных| NEEDS_INFO
CHECKING -->|источник не ответил| MANUAL_REVIEW
CHECKING --> SCORING
NEEDS_INFO -->|POST update и submit| SUBMITTED
NEEDS_INFO -->|POST cancel| CANCELLED
SCORING --> APPROVED
SCORING --> COUNTER_OFFER
SCORING --> MANUAL_REVIEW
SCORING --> REJECTED
MANUAL_REVIEW -->|POST approve| APPROVED
MANUAL_REVIEW -->|POST counter-offer| COUNTER_OFFER
MANUAL_REVIEW -->|POST request-info| NEEDS_INFO
MANUAL_REVIEW -->|POST reject| REJECTED
MANUAL_REVIEW -->|POST recheck| CHECKING
APPROVED -->|POST accept| SIGNING
APPROVED -->|POST cancel или 7 дней| CANCELLED
COUNTER_OFFER -->|POST accept без залога| SIGNING
COUNTER_OFFER -->|POST accept с залогом| NEEDS_INFO
COUNTER_OFFER -->|POST cancel или 7 дней| CANCELLED
SIGNING -->|POST signing/confirm| ISSUED
SIGNING -->|POST cancel| CANCELLED
```

Таблица переходов `ApplicationStateMachine.TRANSITIONS` (всё, чего нет в таблице, запрещено и даёт `InvalidStatus`):

| Из | В | Кто инициирует |
|---|---|---|
| DRAFT | SUBMITTED, CANCELLED | клиент |
| SUBMITTED | CHECKING | система |
| CHECKING | REJECTED, NEEDS_INFO, MANUAL_REVIEW, SCORING | система |
| NEEDS_INFO | SUBMITTED, CANCELLED | клиент |
| SCORING | APPROVED, COUNTER_OFFER, MANUAL_REVIEW, REJECTED | система |
| MANUAL_REVIEW | APPROVED, COUNTER_OFFER, NEEDS_INFO, REJECTED, CHECKING | аналитик |
| APPROVED | SIGNING | клиент |
| APPROVED | CANCELLED | клиент или `ExpirationJob` |
| COUNTER_OFFER | SIGNING, NEEDS_INFO | клиент |
| COUNTER_OFFER | CANCELLED | клиент или `ExpirationJob` |
| SIGNING | ISSUED, CANCELLED | клиент |

- Активные статусы: все, кроме `ISSUED`, `REJECTED`, `CANCELLED` (SCOPE, раздел 8).
- `ApplicationStateMachine.allowed_actions(app, role)` возвращает список действий клиента: `DRAFT` и `NEEDS_INFO` дают
  `EDIT`, `UPLOAD_STATEMENT`, `SUBMIT`, `CANCEL`; `APPROVED` и `COUNTER_OFFER` дают `ACCEPT`, `CANCEL`; `SIGNING` даёт `SIGN`, `CANCEL`.
  Для аналитика список пустой: его решения идут отдельными эндпоинтами. Фронтенд берёт кнопки из этого списка,
  чтобы не дублировать логику статусов.

---

## 6. Конвейер: проверки

Порядок важен: стоп-фактор раньше дозапроса, дозапрос раньше скоринга, поводы ручной проверки после скоринга (SCOPE, раздел 4).

```mermaid
classDiagram
direction TB
class PipelineOrchestrator {
  -collector: DataCollector
  -stop_rules: list~StopFactorRule~
  -verifier: StatementVerifier
  -scoring: ScoringModel
  -decision: DecisionEngine
  -state: ApplicationStateMachine
  -publisher: StatusPublisher
  -lock: PipelineLock
  +run(application_id, run_id) async
  -checking(collected) CheckingOutcome
  -score_and_decide(ctx) Decision
}
class DataCollector {
  -ports: SourcePorts
  +collect(app, on_step) CollectedData
}
class CollectedData {
  +data: CheckingData
  +snapshots: list~SourceSnapshot~
  +missing() set~SourceType~
  +context() PipelineContext
}
class CheckingData {
  +application: Application
  +company: CompanyProfile?
  +credit_report: CreditReport?
  +related_credit_reports: list~CreditReport~
  +turnover: MonthlyTurnover?
  +statement: BankStatement?
  +tax: TaxInfo?
  +bailiff: BailiffInfo?
  +director_bailiff: BailiffInfo?
  +cbr_level: KycLevel?
  +rosfin_hit: bool?
  +reported_annual_revenue: Money?
  +missing: set~SourceType~
  +now: datetime
}
class PipelineContext {
  +application: Application
  +company: CompanyProfile
  +credit_report: CreditReport
  +related_credit_reports: list~CreditReport~
  +turnover: MonthlyTurnover
  +statement: BankStatement
  +tax: TaxInfo
  +bailiff: BailiffInfo
  +director_bailiff: BailiffInfo
  +cbr_level: KycLevel
  +rosfin_hit: bool
  +reported_annual_revenue: Money
  +statement_verification: Verification
  +now: datetime
  +today() date
  +annual_revenue() Money
  +monthly_revenue() Money
  +revenue_history_months() int
}
class CheckingOutcome {
  +kind: OK, STOP, NEEDS_INFO, SOURCE_DOWN
  +checks: list~CheckResult~
  +info_items: list~str~
}
class StopFactorRule {
  <<abstract>>
  +code: str
  +requires: set~SourceType~
  +check(data: CheckingData) CheckResult
}
class StatementVerifier {
  -strategies: list~AuthenticityStrategy~
  +check_period(statement, today) CheckResult
  +verify(statement, ctx) Verification
}
class AuthenticityStrategy {
  <<interface>>
  +supports(company) bool
  +verify(statement, ctx) CheckResult
}
PipelineOrchestrator --> DataCollector
PipelineOrchestrator --> StopFactorRule
PipelineOrchestrator --> StatementVerifier
DataCollector ..> CollectedData : создаёт
CollectedData ..> PipelineContext : только если ответили все
PipelineOrchestrator ..> CheckingOutcome
StopFactorRule <|-- CompanyInactiveRule
StopFactorRule <|-- BusinessAgeUnder6mRule
StopFactorRule <|-- CurrentOverdueOver30Rule
StopFactorRule <|-- ForbiddenOkvedRule
StopFactorRule <|-- TaxBlockOrBailiffRule
StopFactorRule <|-- CbrRedLevelRule
StopFactorRule <|-- RosfinListRule
StopFactorRule <|-- DirectorDisqualifiedRule
StatementVerifier --> AuthenticityStrategy
AuthenticityStrategy <|.. BankSignatureStrategy
AuthenticityStrategy <|.. FnsRevenueReconciliationStrategy
```

`CheckingData` (`app/domain/checks/context.py`) несёт то, что удалось собрать: поле источника, который не ответил
или не запрашивался, пустое, а сам источник есть в `missing`. По нему работают стоп-факторы, чтобы отчёт аналитика
собирался и при неполных данных (SCOPE, раздел 4: «в отчёте видно, каких данных нет»).

`PipelineContext` (там же) это полный набор данных, его поля обязательные. Он собирается из `CheckingData`
(`CollectedData.context()`), только если ответили все источники, и только его получают проверка выписки,
скоринг (контракт раздела 7 не меняется), поводы ручной проверки и решение. Все проверки синхронные и не ходят в порты.
Поля сверх очевидных:

- `related_credit_reports`: отчёты БКИ директора и учредителей; кого учитывать (доля от 20%), решает `CurrentOverdueOver30Rule`;
- `director_bailiff`: производства ФССП директора, только у ООО (у ИП они уже в `bailiff`);
- `reported_annual_revenue`: выручка по ГИР БО за прошлый год, для сверки выписки;
- `statement_verification`: результат `StatementVerifier.verify`, `DataCollector` кладёт его через `dataclasses.replace`;
- `now`: время сборки контекста (из `Clock`), отсюда `today` и `Decision.decided_at`;
- `annual_revenue()`: обороты по счёту в нашем банке, иначе выписка, оба приведены к году.

### Этап 1. Форма (`FormValidator`, до отправки, синхронно)

Ошибки полей возвращаются разом как `422 VALIDATION_ERROR` с `details` по полям. Исключения по `docs/API.md`:
`APPLICANT_IS_DIRECTOR` даёт `403 NOT_DIRECTOR`, `NO_ACTIVE_APPLICATION` даёт `409 ACTIVE_APPLICATION_EXISTS`.
В конвейер такая заявка не попадает. Для ИП `CONSENTS_GIVEN` принимает одно согласие БКИ любого типа плюс ПДн.

| Код проверки | Условие | Источник |
|---|---|---|
| `FORM_COMPLETE` | все обязательные поля есть и в верном формате | анкета |
| `TERMS_IN_RANGE` | сумма и срок в границах варианта из `products.yaml` | `LoanProduct.validate_terms` |
| `PURPOSE_ALLOWED` | продукт допустим для цели (ВКЛ только для оборотных средств) | `products.yaml` |
| `APPLICANT_IS_DIRECTOR` | пользователь из JWT это руководитель по ЕГРЮЛ или сам ИП | аккаунт + мок ЕГРЮЛ |
| `NO_ACTIVE_APPLICATION` | нет другой активной заявки по ИНН | репозиторий |
| `COLLATERAL_COVERAGE` | для залога оценка не меньше 50% суммы | анкета |
| `OWNER_GUARANTEE` | для ООО без залога дано согласие собственника | анкета |
| `CONSENTS_GIVEN` | согласия на БКИ (ООО: компания и руководитель, ИП: одно) и на ПДн | согласия |
| `STATEMENT_UPLOADED` | если счёта в банке нет, загружена выписка | мок оборотов |
| `STATEMENT_INN_MATCH` | ИНН в выписке совпадает с ИНН заявки | парсер 1С |

### Этап 2. CHECKING (`PipelineOrchestrator.checking`)

1. `DataCollector` (в worker) запрашивает источники через порты, каждый обёрнут в `RetryingGateway`
   (`SOURCE_RETRY_ATTEMPTS` попыток с паузой). Результат каждого запроса сохраняется в `SourceSnapshot`.
   Источник, не ответивший после всех попыток, получает снимок `FAILED` с `attempts = SOURCE_RETRY_ATTEMPTS`
   и `payload = {"error": "Нет ответа после 3 попыток"}` (число из настройки), а сбор идёт дальше.
   Если не ответил ЕГРЮЛ, источники по ИНН компании всё равно опрашиваются (БКИ компании, обороты, ФНС,
   ГИР БО по `Application.company_kind`, ФССП компании, ЦБ). Запросы, которым нужны данные ЕГРЮЛ, не делаются:
   БКИ директора и учредителей, ФССП директора и Росфинмониторинг (он ищет по ФИО, проверка по одному ИНН дала бы
   ложное «не найдены»). Для них пишется снимок `SKIPPED` с `attempts = 0` и
   `payload = {"error": "Не запрашивали: нет ответа ЕГРЮЛ"}`.
2. Стоп-факторы считаются всегда, по тем данным, что есть. Правило, которому не хватает источника из `requires`,
   даёт `CheckOutcome.NO_DATA` с `actual = "нет ответа"`. Для каждого источника из `missing` добавляется строка
   `SOURCE_{source}` с `NO_DATA`, как раньше.
3. Если хотя бы один источник не ответил или не запрашивался (любой, включая обороты и ГИР БО, по SCOPE раздел 4):
   переход в `MANUAL_REVIEW` без скоринга с причиной `SOURCE_UNAVAILABLE`. Это так даже тогда, когда на неполных
   данных сработал стоп-фактор: автоотказа нет, аналитик видит стоп-фактор в отчёте и может отказать сам.
   Дозапрос по выписке в этом случае тоже не делается, решает аналитик.
4. Все источники ответили: стоп-факторы по порядку, первый сработавший даёт `REJECTED` с причиной:

| Класс | Стоп-фактор (SCOPE, раздел 4) | Источник (`requires`) |
|---|---|---|
| `CompanyInactiveRule` | ликвидация, банкротство, реорганизация | ЕГРЮЛ/ЕГРИП (`EGRUL`) |
| `BusinessAgeUnder6mRule` | бизнесу меньше 6 месяцев | ЕГРЮЛ/ЕГРИП (`EGRUL`) |
| `CurrentOverdueOver30Rule` | текущая просрочка больше 30 дней у компании, ИП, директора или учредителя от 20% | БКИ (`EGRUL`, `BKI`: без ЕГРЮЛ неизвестны директор и учредители) |
| `ForbiddenOkvedRule` | игорный бизнес, производство алкоголя | ЕГРЮЛ/ЕГРИП (`EGRUL`) |
| `TaxBlockOrBailiffRule` | блокировка счетов ФНС или неисполненные производства ФССП | ФНС, ФССП (`FNS`, `FSSP`) |
| `CbrRedLevelRule` | красный уровень на платформе ЦБ «Знай своего клиента» | ЦБ (`CBR_KYC`) |
| `RosfinListRule` | компания, директор или учредитель в перечне Росфинмониторинга | Росфинмониторинг (`ROSFIN`) |
| `DirectorDisqualifiedRule` | руководитель дисквалифицирован | «Прозрачный бизнес» (`FNS`) |

Источник считается отсутствующим, если хотя бы один его снимок `FAILED` или `SKIPPED` (у БКИ и ФССП снимков
несколько: компания, директор, учредители).

Коды (`StopFactorRule.code`, они же `CheckResult.code` и причина отказа): `COMPANY_INACTIVE`, `BUSINESS_AGE_UNDER_6M`,
`CURRENT_OVERDUE_OVER_30`, `FORBIDDEN_OKVED`, `TAX_BLOCK_OR_BAILIFF`, `CBR_RED_LEVEL`, `ROSFIN_LIST`, `DIRECTOR_DISQUALIFIED`.
Список по умолчанию в порядке SCOPE: `default_stop_rules(catalog)`.

5. Дозапрос (`NEEDS_INFO` + `InfoRequest`): `StatementVerifier.check_period` (код `STATEMENT_PERIOD`) даёт `FAILED`,
   если выписка короче 6 месяцев или последняя операция старше месяца.
6. `StatementVerifier.verify` сохраняет результат проверки подлинности выписки (повод для ручной проверки применяется после скоринга):
   - `BankSignatureStrategy`: подпись банка есть и верна (мок ЭП);
   - `FnsRevenueReconciliationStrategy` (только ООО): выручка по выписке, приведённая к году, отличается от ГИР БО не больше чем на 20%;
     без отчётности в ГИР БО результат `NO_DATA`.

   Выписка подтверждена, если её подтвердила хотя бы одна применимая стратегия.
7. Иначе переход в `SCORING`. Если `ScoringEngine` бросает `ScoringNotImplemented` (скоринг пока заглушка), заявка уходит в `MANUAL_REVIEW` с причиной `SCORING_NOT_IMPLEMENTED`, решение не вызывается (раздел 7).

Если мок «сломан» для ИНН через `/dev/mocks/failures`, он бросает `SourceUnavailable` на каждой попытке.

### Этап 3. После скоринга (`ManualReviewTrigger`)

Заявку не отклоняем и не одобряем автоматически. Класс и рекомендация системы всё равно считаются и видны аналитику
(когда скоринг реализован).

| Класс | Повод |
|---|---|
| `SecuredVariantTrigger` | вариант с залогом |
| `UnverifiedStatementTrigger` | выписка из другого банка не подтверждена |
| `AmountOver5mTrigger` | сумма больше 5 000 000 |
| `RecentOwnerChangeTrigger` | смена директора или собственника меньше 6 месяцев назад |
| `UnreliableRegistryRecordTrigger` | запись о недостоверности сведений в ЕГРЮЛ |
| `CbrYellowLevelTrigger` | жёлтый уровень на платформе ЦБ «Знай своего клиента» |
| `TaxDebtOver10kTrigger` | задолженность компании или ИП по налогам больше 10 000 |
| `DirectorBailiffOver10kTrigger` | производства ФССП у директора как физлица больше 10 000 |

Коды причин (`ManualReviewTrigger.reason_code`, в API это `manual_reasons`): `SECURED_VARIANT`, `STATEMENT_UNVERIFIED`,
`AMOUNT_OVER_5M`, `RECENT_OWNER_CHANGE`, `UNRELIABLE_REGISTRY_RECORD`, `CBR_YELLOW_LEVEL`, `TAX_DEBT_OVER_10K`,
`DIRECTOR_BAILIFF_OVER_10K`. Выписка есть, но не проверялась, считается неподтверждённой.
Список по умолчанию в порядке SCOPE: `default_manual_review_triggers()`.

---

## 7. Скоринг (сейчас заглушка, модель делает коллега)

**Текущий статус.** Модель скоринга пока не реализуется. В коде есть только интерфейс и пустой класс,
чтобы конвейер, решение, отчёт и API уже работали с `ScoringResult`. Модель добавит коллега,
не меняя остальной код: достаточно реализовать `ScoringEngine.score`.

```mermaid
classDiagram
direction TB
class ScoringModel {
  <<abstract>>
  +score(ctx) ScoringResult
}
class ScoringEngine {
  +score(ctx) ScoringResult
}
class FixedScoringEngine {
  -results_by_inn: dict
  +score(ctx) ScoringResult
}
class ScoringResult {
  +total: int
  +risk_class: RiskClass
  +debt_load: float
  +factors: list~FactorScore~
}
class FactorScore {
  +code: str
  +label: str
  +value: str
  +points: int
}
ScoringModel <|-- ScoringEngine
ScoringModel <|-- FixedScoringEngine
ScoringModel ..> ScoringResult
ScoringResult "1" *-- "*" FactorScore
```

| Класс | Где | Что делает сейчас |
|---|---|---|
| `ScoringModel` | `app/domain/scoring/model.py` | абстрактный класс, единственный метод `score(ctx: PipelineContext) -> ScoringResult` |
| `ScoringEngine` | `app/domain/scoring/engine.py` | заглушка: тело `raise ScoringNotImplemented`. Именно его подключает `main.py` |
| `FixedScoringEngine` | `tests/fakes/scoring.py` | только для тестов: отдаёт заранее заданный `ScoringResult` по ИНН |
| `ScoringResult`, `FactorScore` | `app/domain/scoring/result.py` | готовые value objects, их используют решение, отчёт и API |
| `ScoringNotImplemented` | `app/domain/errors.py` | исключение заглушки |

Поведение конвейера, пока скоринг заглушка: `PipelineOrchestrator` переводит заявку в `SCORING`, вызывает
`ScoringEngine.score`, ловит `ScoringNotImplemented` и переводит заявку в `MANUAL_REVIEW` с причиной
`SCORING_NOT_IMPLEMENTED`. `DecisionEngine` в этом случае не вызывается, `ScoringResult` в заявке пустой,
в отчёте и в ответах API поле `scoring` отсутствует. Аналитик принимает решение вручную, ставку выбирает
в диапазоне варианта. Так демо проходит от заявки до выдачи и без модели.

### Контракт для будущей модели (для коллеги)

- Реализовать `ScoringEngine.score(ctx)` и вернуть `ScoringResult` с разбивкой по факторам.
  Клиент и аналитик видят разбивку, поэтому `FactorScore.label` и `value` пишутся по-человечески.
- Классы риска: 70 и выше A, 50-69 B, 30-49 C, меньше 30 D (SCOPE, раздел 5). `DecisionEngine` опирается только на `risk_class`.
- `debt_load` это доля от 0 до 1, в API отдаётся процентами.
- Все данные модель берёт из `PipelineContext`: компания, кредитный отчёт БКИ, обороты или выписка, налоговые данные.
  Новые источники добавляются через порт и мок, а не прямыми вызовами.
- Структура по желанию автора модели. Рекомендуемая (ООП, расширяемо): абстрактный `ScoringFactor.evaluate(ctx) -> FactorScore`,
  по классу на фактор из SCOPE раздел 5, `RiskClassifier`, `AnnuityCalculator` для платежа по новому кредиту.
- Готовность модели проверяется так: подменить `FixedScoringEngine` на настоящий `ScoringEngine` в e2e
  и получить баллы и классы из таблицы ниже.

Факторы из SCOPE раздел 5, на которые рассчитана модель (справочно, для коллеги): возраст бизнеса, долговая нагрузка,
сумма к годовой выручке, кредитная история, закрытые кредиты без просрочек, отраслевой риск по ОКВЭД,
динамика выручки, адрес массовой регистрации, частые обращения за кредитами.

### Результаты скоринга для `FixedScoringEngine` (SCOPE, раздел 12)

| Сценарий | Компания | Баллы | Класс | Ожидаемый исход с этим классом |
|---|---|---|---|---|
| 1 | ООО «Пекарня Колос» | 90 | A | одобрено, 23,5% |
| 2 | ООО «Ромашка» | не вызывается | | отказ по стоп-фактору (ликвидирована) |
| 3 | ИП Соколов | 55 | B | контрпредложение 26,5%: 3,6 млн без залога или 4,5 млн с залогом |
| 4 | ИП Иванова | 50 | B | дозапрос выписки, затем одобрено 26,5% |
| 5 | ООО «Техпром» | 100 | A | ручная проверка (залог и сумма больше 5 млн), аналитик одобряет 23% |

Для правила лимита от выручки (сценарий 3) нужна годовая выручка, она берётся из `PipelineContext`, а не из скоринга.

---

## 8. Решение

```mermaid
classDiagram
direction TB
class DecisionEngine {
  -rules: list~DecisionRule~
  -triggers: list~ManualReviewTrigger~
  -rates: RatePolicy
  +decide(ctx, scoring) Decision
}
class ManualReviewTrigger {
  <<abstract>>
  +reason_code: str
  +applies(ctx, scoring) bool
}
class DecisionRule {
  <<abstract>>
  +order: int
  +matches(ctx, scoring, reasons) bool
  +apply(ctx, scoring, reasons) Decision
}
class RatePolicy {
  +rate_for(variant, risk_class) Decimal
  +validate_manual(variant, rate) None
}
DecisionEngine --> ManualReviewTrigger
DecisionEngine --> DecisionRule
DecisionEngine --> RatePolicy
DecisionRule <|-- ManualReviewRule
DecisionRule <|-- ClassDRejectRule
DecisionRule <|-- RevenueLimitCounterOfferRule
DecisionRule <|-- ClassAApproveRule
DecisionRule <|-- ClassBApproveRule
DecisionRule <|-- ClassCCounterOfferRule
```

Правила применяются по порядку, первое сработавшее даёт решение (SCOPE, раздел 6):

| `order` | Класс | Условие | Решение |
|---|---|---|---|
| 1 | `ManualReviewRule` | есть хотя бы один повод ручной проверки | `MANUAL_REVIEW` с причинами |
| 2 | `ClassDRejectRule` | класс D | `REJECTED` с ключевыми факторами |
| 3 | `RevenueLimitCounterOfferRule` | класс A или B и сумма больше лимита от выручки для варианта | `COUNTER_OFFER`: сумма в пределах лимита без залога, плюс запрошенная сумма с залогом, если она укладывается в лимит варианта с залогом |
| 4 | `ClassAApproveRule` | класс A | `APPROVED` на запрошенных условиях |
| 5 | `ClassBApproveRule` | класс B | `APPROVED` на запрошенных условиях |
| 6 | `ClassCCounterOfferRule` | класс C | `COUNTER_OFFER` с условиями, при которых `ScoringModel` даёт класс B или выше: максимальная сумма (шаг 100 000 ₽, не выше лимита от выручки) на запрошенный срок и минимальный срок на запрошенную сумму. Ставка по новому классу |

- `rule_code` в `Decision`: `MANUAL_REVIEW`, `CLASS_D_REJECT`, `REVENUE_LIMIT_COUNTER_OFFER`, `CLASS_A_APPROVE`,
  `CLASS_B_APPROVE`, `CLASS_C_COUNTER_OFFER`. Порядок задаёт `order` правила, а не позиция в списке; повтор `order` это ошибка.
- `RatePolicy`: A это нижняя граница диапазона варианта, B середина, C верхняя граница. В контрпредложении у каждого
  варианта своя ставка по классу (сценарий 3: 26,5% без залога, 24,5% с залогом), `Decision.rate` равна ставке первого варианта.
- `MANUAL_REVIEW`: в `Decision.rate` ставка по классу как подсказка аналитику (для D её нет).
- `REJECTED` по классу D: в `reasons` коды факторов с отрицательными баллами, худший первым (или `RISK_CLASS_D`).
  Антифрод-факторы (`FactorScore.client_visible = False`) в `reasons` не попадают.
- `ClassCCounterOfferRule` получает `ScoringModel` в конструкторе и пересчитывает скоринг для кандидатов
  на копии заявки; `default_decision_rules(catalog, rates, scoring)`.
- Если ни один вариант контрпредложения не проходит границы сумм продукта, решение `MANUAL_REVIEW`
  с причиной `NO_COUNTER_OFFER_AVAILABLE`: одобрять запрошенное правило не вправе.
- После ручной проверки ставку назначает аналитик в пределах диапазона варианта, по умолчанию подставляется ставка по классу.
- Если клиент в контрпредложении выбрал вариант с залогом: `COUNTER_OFFER -> NEEDS_INFO`, клиент заполняет залог,
  заявка снова проходит CHECKING и по поводу «залог» попадает на ручную проверку.
- Лимит от выручки: `annual_revenue * revenue_limit_pct / 100` (30% без залога, 50% с залогом), но не больше `amount_max` варианта.

---

## 9. Продукты

```mermaid
classDiagram
direction TB
class ProductCatalog {
  -products: dict~str, LoanProduct~
  +from_config(raw) ProductCatalog$
  +get(code) LoanProduct
  +for_purpose(purpose) list~LoanProduct~
  +recommend(purpose) Recommendation
}
class LoanProduct {
  <<abstract>>
  +config: ProductConfig
  +validate_terms(variant, amount, term) list~CheckResult~
  +revenue_limit(variant, annual_revenue) Money
  +monthly_payment(amount, term, rate) Money
  +open_contract(app, opening) Contract
  +reschedule(contract, rate, term) list~ScheduleItem~
  +allowed_operations() set~OperationType~
}
class LumpSumLoan {
  +open_contract() LoanContract
  +build_schedule(amount, term, rate, start) list~ScheduleItem~
  +recalc_after_prepayment(contract)
}
class RevolvingCreditLine {
  +tranche_min = 50000
  +tranche_term_months = 6
  +open_contract() CreditLineContract
  +draw_until(contract) date
  +can_draw(contract, amount, today) bool
}
ProductCatalog "1" o-- "*" LoanProduct
LoanProduct <|-- LumpSumLoan
LoanProduct <|-- RevolvingCreditLine
```

`app/infra/config/products.yaml` (числа из SCOPE, раздел 2). Домену разрешены только stdlib и Pydantic, поэтому YAML
читает infra, а `ProductCatalog.from_config` получает уже разобранный словарь и валидирует его Pydantic-моделями
(`CatalogConfig`). Первый платёж графика `build_schedule` приходится через месяц после `start`, даты прижимаются к концу месяца.
У каждой цели есть `explanation`: это объяснение для клиента, почему система рекомендует продукт (SCOPE, раздел 2).

```yaml
purposes:
  ONE_TIME_PURCHASE: {label: "Разовая закупка",        products: [LUMP_SUM, REVOLVING_LINE], recommended: LUMP_SUM}
  DEVELOPMENT:       {label: "Развитие",               products: [LUMP_SUM],                 recommended: LUMP_SUM}
  REFINANCING:       {label: "Рефинансирование",       products: [LUMP_SUM],                 recommended: LUMP_SUM}
  REGULAR_PURCHASES: {label: "Регулярные закупки",     products: [LUMP_SUM, REVOLVING_LINE], recommended: REVOLVING_LINE}
  SEASONAL_GAP:      {label: "Сезонный кассовый разрыв", products: [LUMP_SUM, REVOLVING_LINE], recommended: REVOLVING_LINE}
  # у каждой цели ещё есть explanation: "…" (текст в самом products.yaml)

products:
  LUMP_SUM:
    name: "Кредит одной суммой"
    class: LumpSumLoan
    variants:
      UNSECURED: {amount_min: 300000, amount_max: 30000000,  term_min: 12, term_max: 60,  rate_min: 23.5, rate_max: 29.5, revenue_limit_pct: 30}
      SECURED:   {amount_min: 500000, amount_max: 150000000, term_min: 12, term_max: 120, rate_min: 21.5, rate_max: 27.5, revenue_limit_pct: 50, collateral_coverage_min_pct: 50}
  REVOLVING_LINE:
    name: "Возобновляемая кредитная линия"
    class: RevolvingCreditLine
    tranche_min: 50000
    tranche_term_months: 6
    variants:
      UNSECURED: {amount_min: 300000, amount_max: 30000000, term_min: 18, term_max: 18, rate_min: 23.5, rate_max: 29.5, revenue_limit_pct: 30, auto_prolong: true}
      SECURED:   {amount_min: 500000, amount_max: 60000000, term_min: 60, term_max: 60, rate_min: 23.0, rate_max: 29.0, revenue_limit_pct: 50, collateral_coverage_min_pct: 50, draw_stop_months_before_end: 6}

collateral_types:   # подписи для /dictionaries/collateral-types
  REAL_ESTATE: "Недвижимость"
  VEHICLE: "Транспорт"
  EQUIPMENT: "Оборудование"
  GOODS: "Товары в обороте"

industry_risk:   # первые две цифры ОКВЭД
  low:    ["10", "13", "14", "15", "16", "17", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "62", "63", "86"]
  medium: ["45", "46", "47", "55", "56", "95", "96"]
  high:   ["41", "42", "43", "49", "79"]
forbidden_okved: ["92", "11.01", "11.02", "11.03", "11.04"]
```

Распределение ОКВЭД по группам условное (SCOPE, раздел 5); его можно уточнить, но сценарии из раздела 12 должны давать свои баллы:
56.10 средний, 47.52 средний, 25.62 низкий.

---

## 10. Сервисы приложения, порты, адаптеры

```mermaid
classDiagram
direction TB
class ApplicationService {
  +create_draft(claims, dto) Application
  +update_draft(claims, dto) Application
  +validate(claims, application_id) list~CheckResult~
  +attach_statement(claims, application_id, file) BankStatement
  +delete_statement(claims, application_id, statement_id)
  +statement_url(claims, application_id, statement_id) PresignedUrl
  +submit(claims, application_id) Application
  +accept(claims, application_id, offer_id) Application
  +cancel(claims, application_id, reason) Application
  +get_for_client(claims, application_id) ClientApplicationView
}
class AuthService {
  +register(dto) User
  +login(login, password) AccessToken
}
class AnalystService {
  +queue(filter) Page
  +report(application_id) ApplicationReport
  +approve(claims, application_id, rate, comment)
  +counter_offer(claims, application_id, options, comment)
  +request_info(claims, application_id, items, comment)
  +reject(claims, application_id, reason, comment)
  +recheck(claims, application_id, comment)
  +edit_contract(claims, contract_id, rate, term, comment)
}
class SigningService {
  +issue_code(claims, application_id) SigningCode
  +confirm(claims, application_id, code) Contract
}
class ContractService {
  +get(claims, contract_id) Contract
  +document_url(claims, contract_id, version_no) PresignedUrl
  +draw_tranche(claims, contract_id, amount) Tranche
  +repay_tranche(claims, contract_id, tranche_id, amount)
  +repay(claims, contract_id, kind, amount)
}
class EmulationService {
  +next_month(contract_id) EmulationResult
  +rewind_to(contract_id, date) EmulationResult
  +reset(contract_id) Contract
}
class ConsentService {
  +give(claims, inn, types) list~Consent~
  +list_for(claims, inn) list~Consent~
}
class CompanyService {
  +get(claims, inn) CompanyView
  +claim_authority(claims, inn, claim) CompanyView
}
class AuthorityService {
  +resolve(applicant, company, claim) ApplicantAuthority
}
class UnitOfWork {
  <<interface>>
  +applications: ApplicationRepository
  +contracts: ContractRepository
  +users: UserRepository
  +consents: ConsentRepository
  +audit: AuditLogRepository
  +commit()
  +rollback()
}
class TaskQueue {
  <<interface>>
  +enqueue(task: PipelineTask) async
}
class StatusPublisher {
  <<interface>>
  +publish(event: ApplicationEvent) async
}
class PipelineLock {
  <<interface>>
  +hold(application_id) async context bool
}
class PipelineReconciler {
  +run_once() async int
}
class FindingsBuilder {
  +build(app) ApplicationFindings
}
class RabbitTaskQueue
class RabbitStatusPublisher
class PipelineConsumer {
  +start() async
  +stop() async
}
class EventSubscriber {
  +start() async
  +stop() async
}
class FileStoragePort {
  <<interface>>
  +put(bucket, key, data, content_type) StoredFile
  +get(bucket, key) bytes
  +delete(bucket, key)
  +presigned_get(bucket, key, ttl_sec, file_name) str
}
class TokenService {
  <<interface>>
  +ttl: timedelta
  +issue(user) str
  +decode(token) TokenClaims
}
class PasswordHasher {
  <<interface>>
  +hash(password) async str
  +verify(hash, password) async bool
}
class JwtService {
  +issue(user) str
  +decode(token) TokenClaims
}
class CurrentUser {
  +__call__(authorization) TokenClaims
  +require_role(role) Depends
}
class WsConnectionManager {
  +connect(websocket, claims) async
  +disconnect(websocket)
  +deliver(event) async
  +send_snapshot(websocket, claims) async
}
class RetryingGateway {
  -inner: Port
  -attempts: int
  -pause_ms: int
}
class MockSourceAdapter {
  <<abstract>>
  #fixtures: SyntheticData
  #failures: MockFailures
  #raise_if_broken(source, inns)
}
ApplicationService --> UnitOfWork
ApplicationService --> TaskQueue
ApplicationService --> StatusPublisher
ApplicationService --> FindingsBuilder : findings
ApplicationService --> FileStoragePort : выписки 1С
ApplicationService --> SignatureVerifierPort : ЭП выписки
ApplicationService --> StatementParser : разбор выписки
AnalystService --> UnitOfWork
AnalystService --> TaskQueue : recheck
AnalystService --> StatusPublisher
PipelineReconciler --> TaskQueue
PipelineConsumer --> PipelineOrchestrator : worker
PipelineOrchestrator --> PipelineLock
EventSubscriber --> WsConnectionManager : api
SigningService --> ContractDocuments : PDF версии 1
AnalystService --> ContractDocuments : PDF новой версии
ContractService --> ContractDocuments : ссылка на PDF
ContractDocuments --> ContractDocumentRenderer
ContractDocuments --> FileStoragePort
AuthService --> TokenService
AuthService --> PasswordHasher
AuthService --> PassportCheckPort
ConsentService --> UnitOfWork
ConsentService --> CompanyRegistryPort
CompanyService --> CompanyRegistryPort
CompanyService --> AccountTurnoverPort
CompanyService --> AuthorityService
AuthorityService --> MchdRegistryPort
AuthorityService --> NotaryPoaRegistryPort
CurrentUser --> TokenService
TokenService <|.. JwtService
PasswordHasher <|.. Argon2PasswordHasher
StatusPublisher <|.. RabbitStatusPublisher
TaskQueue <|.. RabbitTaskQueue
PipelineLock <|.. PgAdvisoryPipelineLock
UnitOfWork <|.. SqlUnitOfWork
FileStoragePort <|.. S3FileStorage
RetryingGateway o-- MockSourceAdapter : оборачивает
```

### Порты источников (`app/domain/ports.py`)

| Порт | Методы | Мок |
|---|---|---|
| `CompanyRegistryPort` | `find_by_inn(inn) -> CompanyProfile` | `MockEgrulAdapter` |
| `CreditBureauPort` | `report(subject_inn, consent) -> CreditReport` (ИНН компании или физлица) | `MockBkiAdapter` |
| `AccountTurnoverPort` | `monthly_turnover(inn, months) -> MonthlyTurnover \| None` (None, если счёта у нас нет) | `MockTurnoverAdapter` |
| `TaxServicePort` | `account_blocks(inn) -> bool`, `is_mass_address(address) -> bool`, `annual_revenue(inn, year) -> Money \| None`, `tax_info(inn) -> TaxInfo` (массовость, дисквалификация, налоговый долг из «Прозрачного бизнеса») | `MockFnsAdapter` |
| `BailiffPort` | `open_proceedings(inn) -> BailiffInfo` | `MockFsspAdapter` |
| `CbrKycPort` | `risk_level(inn) -> KycLevel` | `MockCbrAdapter` |
| `RosfinmonitoringPort` | `is_listed(inn, persons) -> bool` | `MockRosfinAdapter` |
| `SignatureVerifierPort` | `verify(statement_raw) -> SignatureStatus` | `MockSignatureAdapter` |
| `PassportCheckPort` | `is_valid(series, number) -> bool` | `MockPassportAdapter` |
| `MchdRegistryPort` | `find(guid, principal_inn, representative_inn) -> PowerOfAttorney \| None` (реестр МЧД ФНС) | `MockMchdAdapter` |
| `NotaryPoaRegistryPort` | `find(notarized_on, register_number, notary_name) -> PowerOfAttorney \| None` (реестр ФНП) | `MockNotaryPoaAdapter` |

Остальные порты: `UnitOfWork` и репозитории (включая `ConsentRepository`), `TaskQueue`, `StatusPublisher`,
`PipelineLock`,
`FileStoragePort` (`S3FileStorage`), `StatementParser` (`Statement1CParser`), `Clock`, `TokenService` (реализация `JwtService`), `PasswordHasher` (`Argon2PasswordHasher`,
хеширует в отдельном потоке, чтобы не блокировать event loop), `ContractDocumentRenderer`
(`render(ContractDocumentData) -> bytes`, реализация `ReportlabContractRenderer`, собирает PDF в отдельном потоке).

Порты очереди и событий (раздел 11):

| Порт | Методы | Реализация |
|---|---|---|
| `TaskQueue` | `enqueue(PipelineTask)`, `PipelineTask(application_id, run_id)` | `RabbitTaskQueue` (очередь `applications.process`) |
| `StatusPublisher` | `publish(ApplicationEvent)`: только после commit, ошибка доставки логируется и не бросается | `RabbitStatusPublisher` (обмен `applications.events`), общий для api и worker |
| `PipelineLock` | `hold(application_id)`: асинхронный контекстный менеджер, отдаёт `False`, если заявку уже ведёт другой worker | `PgAdvisoryPipelineLock` (`pg_try_advisory_lock` на отдельном соединении) |

`ContractRepository` кроме `get` и `add` умеет `next_sequence()` (последовательность PostgreSQL `contract_number_seq`,
номер договора `КД-{год}-{000001}` у обоих продуктов; номер из откаченной транзакции теряется, повторов нет)
и `list_for(applicant_id?, application_id?)`.

`SqlUnitOfWork` (`app/infra/db`): одна сессия на сценарий. Таблицы в `models.py`, перевод домен ⇄ строки в
`mappers.py`, домен ORM-классов не видит. Репозиторий запоминает прочитанные и добавленные агрегаты, а `commit`
переводит каждый из них в строки целиком и сливает с сессией (`session.merge`): изменённые строки обновляются,
новые вставляются, исчезнувшие из агрегата удаляются. Части без своего id (история, проверки, снимки) имеют ключ
«родитель, позиция». `LoanContract` и `CreditLineContract` лежат в одной таблице `contracts` с колонкой `kind`.

### Моки и синтетика

Все внешние источники в MVP только моки. Реальных HTTP-клиентов к ЕГРЮЛ, БКИ, ФНС, ФССП, ЦБ, Росфинмониторингу
и банкам нет. Реальная интеграция позже подключается новым адаптером того же порта в `main.py`, домен и сервисы не меняются.


- Синтетика лежит в `app/infra/mocks/data/companies/{inn}.json`, по одному набору на ИНН. Секции файла повторяют
  контракты моков из `docs/data-sources`: `egrul`, `bki` (отчёты по компании, директору, учредителям от 20% или ИП),
  `turnover`, `fns_transparent`, `gir_bo` (по годам, в тыс. ₽), `fssp`, `cbr_kyc`. В `fns_transparent` добавлено поле
  `accounts_blocked` для стоп-фактора 5: в контракте «Прозрачного бизнеса» его нет. Общие справочники:
  `rosfin_list.json` (перечень Росфинмониторинга) и `passports.json` (недействительные паспорта).
  `SyntheticData` загружает файлы, проверяет их Pydantic-схемами и сверяет сводные поля с детальными.
  Сейчас пять компаний из SCOPE, раздел 12; сценарии 6 и 7 пока не добавлены.
- Режим «сломаться»: общий реестр `MockFailures` с множеством ИНН на каждый `SourceType`
  (`MockSourceAdapter.raise_if_broken(source, *inns)`). Ключ это источник, а не адаптер, потому что `MockFnsAdapter`
  отвечает и за `FNS`, и за `GIR_BO`. Сломанный ИНН компании ломает и запросы БКИ и ФССП по её директору и учредителям.
  `/dev/mocks/failures` меняет этот реестр. Реестр хранится в таблице `mock_failures(source, inn)` (`SqlMockFailures`,
  `app/infra/db/mock_failures.py`): поломку включает `api`, а источники этапа 2 опрашивает `worker`, память у них
  не общая. In-memory `MockFailures` остаётся для тестов, где оба процесса работают в одном.
- Каждый мок делает задержку `PIPELINE_STEP_DELAY_MS`, чтобы смена статусов была видна на демо.
- `RetryingGateway[P]` (`app/infra/retrying.py`) это декоратор: для каждого порта есть обёртка, реализующая тот же
  порт (`RetryingCompanyRegistry`, `RetryingCreditBureau` и т.д.), поэтому конвейер не знает о повторах.
  `SOURCE_RETRY_ATTEMPTS` это общее число попыток, включая первую, между попытками пауза `SOURCE_RETRY_PAUSE_MS`.
  Повторяется только `SourceUnavailable`; `NotFound` пробрасывается сразу.
- Неизвестный ИНН: ЕГРЮЛ и «Прозрачный бизнес» бросают `NotFound`, остальные источники отвечают «пусто»
  (нет кредитной истории, нет счёта, нет производств, уровень ЦБ зелёный, в перечне нет).
- `Statement1CParser` (`app/infra/parsers/statement_1c.py`, порт `StatementParser`) возвращает доменный
  `ParsedStatement` (`app/domain/entities/parts.py`): счёт, ИНН владельца,
  период, операции, помесячные поступления и выручку, приведённую к году. Направление операции определяется по номеру
  счёта. Ошибка формата это `ValidationFailed` с полем `file` и кодом `STATEMENT_FORMAT` (`422 VALIDATION_ERROR`).
- Подпись банка в синтетических выписках: блок после `КонецФайла`
  (`СекцияПодписьБанка`, `Подписант=`, `ХешДокумента=sha256:<хеш всех байтов до блока>`, `КонецПодписиБанка`).
  `MockSignatureAdapter`: блока нет → `ABSENT`, хеш совпал → `VALID`, иначе `INVALID`.
- Выписки сценария 4 (`data/statements/ivanova_4m.txt`, `ivanova_12m_signed.txt`) создаёт
  `python -m app.infra.mocks.statement_samples --end YYYY-MM-DD`. Даты зашиты в файлы, а свежесть выписки
  проверяется от текущей даты, поэтому перед демо в другой день их перегенерируют.
- `seed.py` создаёт тестовые учётки: по одному руководителю на каждую компанию и одного аналитика.
  Логины и пароли печатаются в README, а не хранятся в коде.

---

## 11. Асинхронная обработка и события

### Процессы

- `api` выполняет этап 1 синхронно: `POST /applications/submit` проверяет форму, переводит заявку в `SUBMITTED`
  с новым `pipeline_run_id`, коммитит, публикует событие и ставит `PipelineTask(application_id, run_id)` в `TaskQueue`.
  Ответ `202`. Если постановка не удалась, ошибка только логируется: заявка уже сохранена, её подхватит reconciler.
  `AnalystService.recheck` делает то же для `MANUAL_REVIEW → CHECKING`.
- `worker` (`python -m app.worker`) потребляет `applications.process` через `PipelineConsumer`
  и вызывает `PipelineOrchestrator.run(application_id, run_id)`. Там же по расписанию (`AsyncioTaskRunner.every`)
  работают `PipelineReconciler` и `ExpirationJob`. В `api` фоновых задач нет.
- Каждый шаг конвейера это отдельная транзакция: `transition -> commit -> publish`.
- Внутри CHECKING worker публикует события `application.step` (какой источник проверяется), они не меняют статус.
- Снимки источников и итог CHECKING сохраняются одной транзакцией: повторная доставка не оставит смесь двух прогонов.

### Топология RabbitMQ (`app/infra/messaging/topology.py`)

| Объект | Тип | Назначение |
|---|---|---|
| `applications.process` | quorum-очередь, durable, `x-delivery-limit = 5`, `x-dead-letter-exchange = applications.dlx` | задачи конвейера. Сообщение persistent, `content_type = application/json`, тело `{"application_id": "…", "run_id": "…"}` |
| `applications.dlx` | fanout-обмен, durable | сюда уходят битые сообщения и сообщения, исчерпавшие число доставок |
| `applications.process.dlq` | quorum-очередь, durable | разбор вручную, никто не потребляет |
| `applications.events` | fanout-обмен, durable | события `ApplicationEvent` (кодек `app/infra/messaging/codec.py`). Каждый процесс `api` объявляет свою exclusive auto-delete очередь, привязанную к обмену |

Топологию объявляют идемпотентно и `api`, и `worker` при старте.

### Обработка задачи (`PipelineConsumer`)

- Подтверждение ручное, `prefetch_count = WORKER_PREFETCH`.
- **ack только после commit** последнего шага и публикации его событий.
- Тело не JSON, нет полей, не UUID, заявки нет: `reject(requeue=False)`, сообщение сразу уходит в DLQ.
- Любая другая ошибка (например, PostgreSQL недоступен): пауза 2 с и `nack(requeue=True)`. В RabbitMQ 4 такой
  возврат увеличивает только `x-acquired-count`, а `x-delivery-count` и `x-delivery-limit` считают лишь аварийные
  возвраты (обрыв канала), поэтому попытки consumer считает сам по большему из двух заголовков. На пятой попытке
  он вызывает `PipelineOrchestrator.give_up`: заявка уходит в `MANUAL_REVIEW` с
  `Decision(MANUAL_REVIEW, reasons=[PIPELINE_FAILED])`, сообщение подтверждается. Если и `give_up` не удался
  (БД недоступна), `reject(requeue=False)` отправляет сообщение в DLQ, заявка остаётся в своём статусе,
  и reconciler поставит её снова.

### Идемпотентность

`PipelineOrchestrator.run(application_id, run_id)`:

1. Захватывает `PipelineLock`, при неудаче повторяет до 10 раз с паузой 1 с: блокировку может держать прогон,
   который уже закоммитил итог и дописывает события, а пришедшая задача от нового прогона (recheck, повторная
   отправка). Не захватил и после этого, значит заявку ведёт другой worker: задача подтверждается без работы.
   Если тот worker упадёт, RabbitMQ доставит его собственное неподтверждённое сообщение заново.
2. Статус не `SUBMITTED`, `CHECKING` или `SCORING`, либо `run_id` не равен `Application.pipeline_run_id`
   (дубль, задача прогона до recheck): задача подтверждается без работы.
3. Иначе прогон продолжается с текущего статуса. `SUBMITTED` переходит в `CHECKING`. В `CHECKING` и `SCORING` данные
   собираются заново, а снимки и проверки этапа заменяются целиком (`record_snapshots`, `record_checks`),
   поэтому повторная доставка не создаёт дублей.

### Reconciler (`PipelineReconciler`, `app/application/pipeline_reconciler.py`)

Заявка не зависает, даже если задача не попала в очередь (сбой RabbitMQ между commit и `enqueue`)
или потерялась. `run_once()` выполняется при старте worker и затем каждые `RECONCILE_INTERVAL_SEC`:
находит заявки в `SUBMITTED`, `CHECKING`, `SCORING` с `updated_at` старше `PIPELINE_STALE_AFTER_SEC`
(`ApplicationRepository.list_stale(statuses, updated_before)`) и ставит `PipelineTask` с их текущим `pipeline_run_id`.
Лишняя задача безопасна благодаря идемпотентности. Порог больше времени обычного прогона (с задержками моков
около 10 секунд), поэтому в штатной работе reconciler дублей не создаёт.

Outbox не используется: reconciler проще, а нужная для него идемпотентность нужна в любом случае из-за повторных доставок.

### События

- `RabbitStatusPublisher` публикует каждое событие в `applications.events`, и в `api` (submit, accept, cancel,
  действия аналитика, подписание), и в `worker` (конвейер, `ExpirationJob`).
- `EventSubscriber` в lifespan `api` читает свою очередь и передаёт события в `WsConnectionManager.deliver`.
  События своего же процесса `api` тоже приходят через RabbitMQ: так WebSocket работает при любом числе экземпляров.
- Ошибка публикации после commit логируется, вызывающий не падает. Потерянное событие клиент восстановит
  по `snapshot` при переподключении и по `POST /applications/get`.
- `ExpirationJob` раз в минуту переводит в `CANCELLED` заявки в `APPROVED` и `COUNTER_OFFER` с истёкшим `expires_at`.
  Время берётся из реального `Clock`, а не из даты договора.
- `StatusChangedEvent`: `application_id`, `applicant_id`, `from_status`, `to_status`, `reason`, `is_final`, `expires_at`, `at`.
- `PipelineStepEvent`: `application_id`, `applicant_id`, `step` (`PipelineStep`), `label`, `at`. Шаги идут в порядке
  `EGRUL, BKI, TURNOVER, FNS, FSSP, CBR_KYC, ROSFIN`, затем `STATEMENT`, если есть выписка. ГИР БО входит в шаг `FNS`.
  Шаг `ROSFIN` публикуется и тогда, когда запрос пропущен из-за ЕГРЮЛ.
  Шаг `SCORING` есть в перечислении, но не отправляется: переход в статус `SCORING` и так виден.
  `StatusPublisher.publish` принимает `ApplicationEvent = StatusChangedEvent | PipelineStepEvent`.
- `reason` системных переходов это машинный код: `PIPELINE_STARTED` (в CHECKING), `CHECKS_PASSED` (в SCORING),
  `SOURCE_UNAVAILABLE`, код стоп-фактора (в REJECTED), `SCORING_NOT_IMPLEMENTED`, причины решения или его `rule_code`,
  `OFFER_EXPIRED` (`ExpirationJob`). Отказ по стоп-фактору сохраняет `Decision` с `rule_code = STOP_FACTOR`.
  Источник не ответил: сохраняется `Decision(MANUAL_REVIEW, reasons=[SOURCE_UNAVAILABLE])`, чтобы очередь аналитика
  показала причину.

### Данные из PostgreSQL

- Ответ каждого источника сохраняется в `SourceSnapshot.payload` (`app/application/source_payload.py`, деньги строками,
  даты ISO). Отчёт аналитика и findings клиента строятся только из PostgreSQL, в источники не ходят.
- Отчёт аналитика (`/analyst/applications/report`) и findings клиента (`/applications/findings`) доступны, когда
  сбор закончен: статус не `SUBMITTED` и не `CHECKING`, а снимки есть. Иначе `409 INVALID_STATUS`.
  В списке аналитика заявка видна с момента `SUBMITTED`.
- Методы `ApplicationRepository`: `list_for_applicant` (список и snapshot клиента),
  `list_by_statuses` (snapshot аналитика), `list_stale` (reconciler), `list_expired` (`ExpirationJob`),
  `list_for_analyst(AnalystFilter, offset, limit)` (список и очередь аналитика, без черновиков).
  `AuditLogRepository.list_for(target_id)` читает аудит по объекту.

### Сборка и тесты

- `main.build_container(settings, engine, ...)` собирает `api`, `main.build_worker(settings, engine, ...)` собирает
  `worker`. e2e-тесты подменяют фабрику `create_app(settings, container_factory=...)`, передают
  `clock=FixedClock` и `scoring=FixedScoringEngine` и запускают обработчик worker в том же процессе
  с in-memory `TaskQueue` и шиной событий из `tests/fakes`. Адаптеры aio-pika проверяются интеграционными тестами
  на настоящем RabbitMQ (`TEST_RABBITMQ_URL`).

---

## 12. Аутентификация (JWT)

- `POST /auth/login` проверяет пароль (argon2) и выдаёт JWT HS256 на `JWT_TTL_MINUTES`. Refresh-токенов в MVP нет.
  Логин сравнивается без учёта регистра. Для несуществующего логина всё равно выполняется проверка пароля
  по фиктивному хешу, чтобы по времени ответа нельзя было узнать, есть ли такой логин.
- Claims:

| Claim | Значение |
|---|---|
| `sub` | id пользователя (UUID строкой) |
| `role` | `CLIENT` или `ANALYST` |
| `name` | ФИО |
| `iat`, `exp` | время выдачи и истечения |
| `jti` | случайный id токена |

- `CurrentUser` (зависимость FastAPI) читает `Authorization: Bearer <token>`, вызывает `JwtService.decode`
  (проверка подписи, `exp`, обязательных claims) и возвращает `TokenClaims` (доменный dataclass:
  `user_id`, `role`, `name`, `issued_at`, `expires_at`, `token_id`).
  Ошибки: `401 TOKEN_MISSING` (заголовка нет или он пустой), `401 TOKEN_INVALID` (схема не `Bearer`, нет токена,
  подпись, алгоритм не HS256, нет claim или он не того типа, пользователь из `sub` удалён),
  `401 TOKEN_EXPIRED` (`now >= exp`). `exp` сверяется с портом `Clock`, а не с системными часами PyJWT.
- `require_role(Role.ANALYST)` даёт `403 FORBIDDEN` при другой роли.
- Владение проверяется в сервисе: клиент видит только свои заявки и договоры (`applicant_id == claims.sub`), аналитик видит всё.
  На чужой объект `403 FORBIDDEN`.
- Регистрация проверяет паспорт через `PassportCheckPort` (мок).

---

## 13. WebSocket

- Путь `/api/v1/ws/applications`.
- Браузер не умеет ставить `Authorization` в WebSocket, поэтому JWT передаётся в заголовке `Sec-WebSocket-Protocol`:
  клиент открывает `new WebSocket(url, ['bearer', token])`.
- Сервер берёт второй элемент подпротокола, расшифровывает токен через `JwtService` до `accept()`
  и принимает соединение с подпротоколом `bearer`. Невалидный токен: закрытие с кодом `4401`.
  Чтобы клиент увидел этот код, сервер сначала принимает соединение и сразу закрывает его:
  закрытие до `accept()` Starlette превращает в HTTP 403.
- Подписки: клиент получает события только своих заявок, аналитик получает события всех заявок.
- Подключения живут в памяти процесса `api` (`WsConnectionManager`). События приходят из RabbitMQ
  через `EventSubscriber` (раздел 11), поэтому клиент получает и события worker, и события другого экземпляра `api`.
- Сразу после подключения сервер присылает `snapshot` (текущие статусы заявок пользователя).
- Сообщения сервера: `snapshot`, `application.status_changed`, `application.step`, `pong`. Сообщение клиента: `ping`.
  Схемы в `docs/API.md`.
- После `application.status_changed` фронт перезапрашивает карточку (`POST /applications/get`).
- При разрыве фронт переподключается (с новым токеном, если старый истёк) и берёт новый `snapshot`.

---

## 14. Файлы в S3

| Что | Бакет | Ключ |
|---|---|---|
| Выписка 1С (оригинал) | `credit-files` | `statements/{application_id}/{statement_id}.txt` |
| PDF договора | `credit-contracts` | `{contract_id}/v{version_no}.pdf` |

- Бакеты приватные. Файл отдаётся presigned GET-ссылкой на `PRESIGNED_TTL_SECONDS` (5 минут),
  ссылка выдаётся только после проверки JWT и владельца.
- Ссылка подписывается на `S3_PUBLIC_ENDPOINT`, чтобы браузер мог по ней ходить.
- Загрузка выписки идёт через API (бэкенд должен разобрать файл): `Statement1CParser` разбирает формат
  `1CClientBankExchange`, при успехе оригинал кладётся в S3, в БД сохраняются `file_key` и операции.
- Договор: при подписании `ContractDocumentRenderer` собирает PDF версии 1, `SigningService` кладёт его в S3
  и сохраняет `document_key` в `ContractVersion`. Каждая новая версия договора (правка аналитиком,
  пересчёт после частичного погашения) получает свой PDF. Старые версии не удаляются.
- Если S3 недоступен при подписании, выдача откатывается целиком (`503 STORAGE_UNAVAILABLE`),
  чтобы не появился договор без документа. Порядок: собрать PDF, положить в S3, затем commit;
  при ошибке commit загруженный файл удаляется.
- Загрузка выписки (`ApplicationService.attach_statement`), порядок проверок: размер больше `MAX_UPLOAD_MB`
  (`413 FILE_TOO_LARGE`) → владелец и статус DRAFT или NEEDS_INFO (`403`, `409`) → разбор (`422 STATEMENT_FORMAT`)
  → ИНН выписки равен ИНН заявки (`422 STATEMENT_INN_MATCH`) → ЭП банка через `SignatureVerifierPort`
  (результат хранится в `BankStatement.signature_status`) → файл в S3 → commit. При ошибке commit файл удаляется.
- Конвейер проверяет последнюю загруженную выписку (по порядку загрузки), старые остаются в заявке для аналитика.
- Удаление выписки удаляет объект из S3 после успешного commit. Если S3 в этот момент недоступен,
  ошибка только логируется: строки в БД уже нет.

---

## 15. Договор, операции, эмуляция

- Договор создаётся при подписании: номер, продукт и вариант, сумма или лимит, срок, ставка, график.
  `LoanContract` для кредита одной суммой, `CreditLineContract` для ВКЛ. Продукт получает в `open_contract`
  `ContractOpening` (id и номер договора, ставку, ключ PDF, кто подписал, текущие время и дату): сам он этого не знает.
  Ставка берётся из выбранного варианта контрпредложения, а если его нет, из решения (`Application.agreed_rate`).
  Дата начала и дата договора это день подписания, первый платёж через месяц. У ВКЛ график пустой:
  проценты и долг считаются по траншам. Версия 1: автор это клиент, `reason` «Договор подписан», `changes` пустые.
- Код подписания (`SigningCode`) хранится в заявке: 6 цифр, живёт `SIGNING_CODE_TTL_SECONDS` (5 минут),
  новый запрос заменяет прежний код, при выходе из `SIGNING` код стирается. Неверный или просроченный код
  даёт `BadSigningCode`. Если выдача откатилась (S3 недоступен), код остаётся действительным.
- Правка аналитиком (`AnalystService.edit_contract`): новая ставка в диапазоне варианта, новый срок в границах варианта,
  график новой версии строится на остаток долга за оставшиеся месяцы с прежними датами платежей
  (`LoanProduct.reschedule`). `ContractVersion.changes` это список `{field, old, new}`, `reason`
  «Условия изменены аналитиком», в журнал пишется операция `CONTRACT_EDITED`. Закрытый договор даёт `InvalidStatus`.
  PDF новой версии кладётся в S3 до commit, как при подписании (раздел 14).
- Любая правка это новая `ContractVersion` (что изменилось, кто, когда, PDF). Увеличение суммы или лимита запрещено,
  только новой заявкой.
- У каждого договора своя дата `contract_date` (`ContractClock`). Эмуляция двигает только её,
  конвейер заявок живёт по реальному времени. Отдельного класса `ContractClock` нет: роль часов договора
  играет поле `Contract.contract_date`, его меняют только `advance_month` и `move_date` агрегата.
  Месячные даты считаются от `start_date` (`add_months`), чтобы 31-е число не съезжало, в том числе
  в графике, перестроенном после погашения или правки. Сдвиг даты идёт по шагам: месячные даты, даты событий
  договора (плановые платежи, сроки траншей) и целевая дата, поэтому каждое событие записывается своим днём.
- Частичное досрочное погашение (`LoanContract.prepay`) создаёт версию с `changes = [{outstanding, old, new}]`,
  `reason` «Частичное досрочное погашение», график на остаток за оставшиеся месяцы (`LoanProduct.reschedule`),
  PDF кладётся в S3 до commit. Последний плановый платёж гасит весь остаток, чтобы копейки округлений не держали договор открытым.

| Операция | Продукт | Кто | Эффект |
|---|---|---|---|
| `DISBURSEMENT` | кредит | система при выдаче | вся сумма зачислена |
| `SCHEDULED_PAYMENT` | кредит | эмулятор | платёж по графику: проценты и часть долга |
| `PARTIAL_PREPAYMENT` | кредит | клиент | долг меньше, график пересчитан (платёж меньше, срок прежний), новая версия |
| `FULL_REPAYMENT` | оба | клиент | долг 0, договор `CLOSED` |
| `LIMIT_OPENED` | ВКЛ | система при выдаче | лимит равен одобренной сумме, доступно равно лимиту |
| `TRANCHE_DRAWN` | ВКЛ | клиент | от 50 000 и не больше доступного остатка, срок 6 мес |
| `INTEREST_ACCRUED` | ВКЛ | эмулятор | ежемесячно по непогашенным траншам |
| `TRANCHE_REPAID` | ВКЛ | клиент или эмулятор в конце срока транша | долг по траншу меньше, доступный лимит восстанавливается |
| `CONTRACT_EDITED` | оба | аналитик или система | новая версия договора |

Эмуляция (`EmulationService`, только при `DEMO_MODE=true`):

- `next_month`: сдвигает дату договора на месяц, проводит плановые платежи, начисляет проценты, гасит истёкшие транши.
- `rewind_to(date)`: то же в цикле до даты, не раньше текущей даты договора и не позже его окончания
  (иначе ВКЛ с автопродлением перематывалась бы бесконечно).
- `reset`: возвращает договор в состояние сразу после выдачи (операции и версии после выдачи удаляются, PDF версии 1 остаётся).
- Денег у клиента всегда хватает, просрочки не эмулируются.

---

## 16. История и аудит

- `StatusTransition`: каждый переход заявки (кто, когда, из какого статуса, причина).
- `AuditLogRepository`: каждое действие аналитика (одобрение, контрпредложение, дозапрос, отказ, повторная проверка,
  правка договора) с обязательным комментарием. `action`: `APPROVE`, `COUNTER_OFFER`, `REQUEST_INFO`, `REJECT`,
  `RECHECK`, `EDIT_CONTRACT`; в `details` всегда `comment` и параметры действия (`rate`, `option_N`, `items`, `reason_code`;
  у `EDIT_CONTRACT` это `version_no`, `rate`, `term_months`, а `target_id` это id договора).
  Запись делается в той же транзакции, что и смена статуса, событие уходит после commit.
- Действия аналитика это методы `Application`: `approve_by_analyst`, `counter_offer_by_analyst`,
  `request_info_by_analyst`, `reject_by_analyst`, `start_recheck`. Каждый требует статус `MANUAL_REVIEW` и непустой
  комментарий и добавляет `Decision` с `rule_code = ANALYST_DECISION`, `decided_by` и `comment` (кроме повторной
  проверки). Одобрение дополнительно требует, чтобы все снимки источников были `OK` (`SourcesNotReady`):
  `FAILED` и `SKIPPED` одинаково требуют повторной проверки.
  Ставку и варианты контрпредложения по каталогу продуктов проверяет `AnalystService` (`RatePolicy.validate_manual`).
  `InfoRequest.item_messages` хранит тексты аналитика к пунктам дозапроса.
- `ReportBuilder.build(app, audience, *, applicant, history, contract)` собирает отчёт по заявке из 7 разделов
  (SCOPE, раздел 7): что дал клиент, что получили из источников, как сравнили, скоринг, что решили, история, договор.
  Данные загружает `AnalystService.report`, сам построитель ничего не читает. Обороты берутся из счёта в нашем банке,
  а без него из последней выписки. Для клиента (`audience=CLIENT`) отдаётся урезанная версия без источников,
  сравнений и оборотов.

---

## 17. Последовательности

### Отправка заявки и конвейер

```mermaid
sequenceDiagram
autonumber
actor C as Клиент (React)
participant API as api: роутер и ApplicationService
participant UOW as PostgreSQL (UnitOfWork)
participant MQ as RabbitMQ
participant W as worker: PipelineConsumer и PipelineOrchestrator
participant SRC as DataCollector и моки
participant SD as ScoringEngine и DecisionEngine
participant WS as api: EventSubscriber и WsConnectionManager
C->>WS: подключение, Sec-WebSocket-Protocol bearer и JWT
WS-->>C: snapshot статусов заявок пользователя
C->>API: POST /applications/submit {application_id}, Authorization Bearer JWT
alt ошибки этапа 1 или чужая заявка
API-->>C: 422 или 403 ErrorOut
else форма валидна
API->>UOW: DRAFT в SUBMITTED, pipeline_run_id, commit
API->>MQ: publish StatusChangedEvent в applications.events
API->>MQ: PipelineTask в applications.process
API-->>C: 202 application_id и status
end
MQ->>WS: событие
WS-->>C: application.status_changed SUBMITTED
MQ->>W: доставка задачи
W->>UOW: PipelineLock, проверка статуса и run_id
W->>UOW: SUBMITTED в CHECKING, commit
W->>MQ: события
MQ->>WS: события
WS-->>C: application.status_changed CHECKING, затем application.step
W->>SRC: collect(app), до 3 попыток на источник
SRC-->>W: CheckingData и снимки (OK, FAILED, SKIPPED)
alt источник не ответил или не запрашивался
W->>UOW: снимки, проверки по имеющимся данным, CHECKING в MANUAL_REVIEW
else стоп-фактор
W->>UOW: снимки, CHECKING в REJECTED с причиной
else выписка короче 6 мес или устарела
W->>UOW: снимки, CHECKING в NEEDS_INFO
else проверки пройдены
W->>UOW: снимки, CHECKING в SCORING
W->>SD: score и decide по PipelineContext
SD-->>W: ScoringResult и Decision
W->>UOW: SCORING в итоговый статус
end
W->>MQ: события после каждого commit
W->>MQ: ack задачи
MQ->>WS: события
WS-->>C: application.status_changed
C->>API: POST /applications/get, /applications/findings
```

Если задача не дошла до очереди, `PipelineReconciler` в worker через `PIPELINE_STALE_AFTER_SEC` ставит её заново.

### Ручная проверка, подписание и выдача

```mermaid
sequenceDiagram
autonumber
actor A as Аналитик
actor C as Клиент
participant API as FastAPI и CurrentUser
participant AN as AnalystService
participant RB as ReportBuilder
participant SG as SigningService
participant LP as LoanProduct
participant CS as ContractService
participant PUB as StatusPublisher
participant S3 as FileStorage S3
Note over A,C: каждый HTTP-запрос несёт Authorization Bearer JWT, роль берётся из токена
A->>API: POST /analyst/applications/list {queue: manual}
A->>API: POST /analyst/applications/report {application_id}
API->>RB: build(app, ANALYST)
RB-->>A: ApplicationReport
A->>API: POST /analyst/applications/approve {application_id, rate, comment}
API->>AN: approve(claims, application_id, rate, comment)
AN->>AN: все источники ответили, ставка в диапазоне
AN->>PUB: publish APPROVED
PUB-->>C: application.status_changed APPROVED
C->>API: POST /applications/accept {application_id}
API-->>C: APPROVED в SIGNING
C->>API: POST /applications/signing/code {application_id}
API->>SG: issue_code(claims, application_id)
SG-->>C: code и expires_at, код показывается в UI
C->>API: POST /applications/signing/confirm {application_id, code}
API->>SG: confirm(claims, application_id, code)
SG->>LP: open_contract(app, opening: номер, ставка, ключ PDF, today)
LP-->>SG: LoanContract или CreditLineContract
SG->>S3: put PDF договора, contract_id/v1.pdf
S3-->>SG: StoredFile
SG->>CS: сохранить договор, версию 1 и операцию выдачи или открытия лимита, commit
SG->>PUB: publish ISSUED
SG-->>C: status ISSUED и contract_id
C->>API: POST /contracts/document-url {contract_id}
API->>S3: presigned_get на 5 минут
API-->>C: url и expires_at
```

---

## 18. Ошибки

Доменные исключения наследуются от `DomainError` и переводятся в HTTP в одном обработчике в `main.py`:

| Исключение | HTTP | `code` |
|---|---|---|
| `ValidationFailed` | 422 | `VALIDATION_ERROR` (с `details`) |
| `TokenMissing`, `TokenInvalid`, `TokenExpired` | 401 | `TOKEN_MISSING`, `TOKEN_INVALID`, `TOKEN_EXPIRED` |
| `InvalidCredentials` | 401 | `INVALID_CREDENTIALS` |
| `LoginTaken` | 409 | `LOGIN_TAKEN` |
| `Forbidden`, `NotDirector` | 403 | `FORBIDDEN`, `NOT_DIRECTOR` |
| `NotFound` | 404 | `NOT_FOUND` |
| `InvalidStatus` | 409 | `INVALID_STATUS` |
| `ActiveApplicationExists` | 409 | `ACTIVE_APPLICATION_EXISTS` |
| `SourcesNotReady` | 409 | `SOURCES_NOT_READY` |
| `OfferExpired` | 410 | `OFFER_EXPIRED` |
| `BadSigningCode` | 400 | `BAD_SIGNING_CODE` |
| `FileTooLarge` | 413 | `FILE_TOO_LARGE` |
| `SourceUnavailable` | 503 | `SOURCE_UNAVAILABLE` |
| `StorageUnavailable` | 503 | `STORAGE_UNAVAILABLE` |

Ошибки валидации Pydantic тоже приводятся к `ErrorOut` с `code=VALIDATION_ERROR`.

---

## 19. Решения сверх SCOPE и что исключено

Решения, принятые командой и отсутствующие в SCOPE:

- Все параметры API передаются в теле POST, без path и query параметров.
- JWT в заголовке на каждом запросе, пользователь и роль только из токена.
- Статус заявки по WebSocket вместо опроса (SCOPE допускал опрос).
- Отдельный процесс `worker` и RabbitMQ (очередь задач, обмен событий) вместо фоновых задач внутри `api`.
  Зависшие заявки подхватывает reconciler, outbox не используется (раздел 11).
- Стоп-факторы считаются и на неполных данных, чтобы отчёт собирался, даже если какой-то источник не ответил.
  Автоотказа на неполных данных нет (раздел 6).
- Клиент видит собранные факты без проверок (`/applications/findings`). Что не так, ему пишет аналитик
  через дозапрос.
- Выписки и PDF договоров хранятся в S3.
- Python 3.15.

Временно не реализуется:

- Модель скоринга (SCOPE, раздел 5): только интерфейс и заглушка, реализацию добавит коллега (раздел 7).
- Реальные интеграции: все источники только моки (SCOPE, раздел 9, это и так допущение MVP).

Исключено из реализации по решению команды:

- Предварительные предложения при входе (SCOPE, раздел 2, Should): эндпоинта и `PrecheckService` нет,
  скоринг работает только в одном режиме.

Отложено (Could в SCOPE): ИИ-подсказка, дифференцированный график, третий продукт, API другого банка,
распознавание сканов, подача по доверенности.
