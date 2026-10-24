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
| Веб | FastAPI, Pydantic v2, uvicorn |
| БД | PostgreSQL 16, SQLAlchemy 2 async + asyncpg, Alembic |
| Файлы | S3-совместимое хранилище, локально MinIO, клиент aioboto3 |
| Авторизация | JWT HS256 (PyJWT), пароли argon2 |
| PDF договора | любая чистая Python-библиотека для генерации PDF (выбор фиксируется на этапе 8) |
| Тесты и качество | pytest, pytest-asyncio, httpx, ruff, mypy strict |
| Запуск | Docker Compose |

### Docker Compose

| Сервис | Назначение |
|---|---|
| `api` | FastAPI-приложение, внутри же фоновые задачи и WebSocket |
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
    INF["infra<br/>SQLAlchemy, моки источников, RetryingGateway, S3, JWT,<br/>WsConnectionManager, парсер 1С, фоновые задачи, products.yaml"]
    API -->|вызывает| APP
    APP -->|управляет| DOM
    INF -.->|реализует порты| DOM
```

Правила:

- Роутер принимает DTO из тела POST, получает `TokenClaims` из `Depends(current_user)`, вызывает сервис, возвращает DTO.
- Пользователь и роль никогда не приходят в теле запроса, только из токена.
- Сервис открывает `UnitOfWork`, загружает агрегат, вызывает методы домена, коммитит, затем публикует события.
- Домен не импортирует FastAPI, SQLAlchemy, aioboto3, PyJWT и моки.
- Реализации подставляются в порты в `app/main.py` (composition root).

```text
app/
  main.py                    # FastAPI, DI, роутеры, обработчики ошибок
  api/v1/
    auth.py  catalog.py  companies.py
    applications.py  analyst.py
    contracts.py  emulation.py  dev.py
    ws.py                    # WebSocket /ws/applications
    schemas/                 # Pydantic DTO, тела всех POST
    deps.py                  # current_user из JWT, require_role, get_uow, сервисы
  application/
    auth_service.py
    application_service.py  form_validator.py
    pipeline.py              # PipelineOrchestrator, DataCollector
    analyst_service.py  report_builder.py
    signing_service.py  contract_service.py  emulation_service.py
  domain/
    entities/                # Application, Contract и их части
    value_objects.py         # Money, Period, Collateral, CompanyProfile...
    enums.py  errors.py  events.py
    state_machine.py
    checks/                  # StopFactorRule, ManualReviewTrigger, StatementVerifier
    scoring/                 # ScoringModel, ScoringEngine (заглушка), ScoringResult
    decision/                # DecisionEngine, DecisionRule, RatePolicy
    products/                # LoanProduct, LumpSumLoan, RevolvingCreditLine, AnnuityCalculator, ProductCatalog
    ports.py                 # порты источников, репозиториев, UoW, Clock, StatusPublisher, FileStoragePort
  infra/
    db/                      # модели, репозитории, SqlUnitOfWork
    mocks/                   # адаптеры источников, data/ с синтетикой, seed.py
    parsers/statement_1c.py
    storage/s3.py            # S3FileStorage
    documents/contract_pdf.py
    security/jwt.py          # JwtService
    realtime/ws_manager.py   # WsConnectionManager
    tasks.py                 # AsyncioTaskRunner, ExpirationJob
    config/products.yaml
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
}
class Application {
  +id: UUID
  +applicant_id: UUID
  +inn: str
  +company_kind: CompanyKind
  +purpose: Purpose
  +product_code: str
  +variant: Variant
  +amount: Money
  +term_months: int
  +owner_guarantee: bool
  +status: ApplicationStatus
  +expires_at: datetime
  +accepted_offer_id: UUID
  +create_draft(form, product, now)$
  +update_form(form, product, now)
  +submit(product, actor, now)
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
  +changes: dict
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
- Каждый переход статуса добавляет `StatusTransition` и событие `StatusChangedEvent`
  во внутренний список событий. Сервис забирает события через `pull_events()` после `commit`.

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
| `FetchStatus` | `OK`, `FAILED` |
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
  +run(application_id) async
  -checking(ctx) CheckingOutcome
  -score_and_decide(ctx) Decision
}
class DataCollector {
  -ports: SourcePorts
  +collect(app) PipelineContext
  +missing_sources(ctx) list~SourceType~
}
class PipelineContext {
  +application: Application
  +company: CompanyProfile
  +credit_report: CreditReport
  +turnover: MonthlyTurnover
  +statement: BankStatement
  +tax: TaxInfo
  +bailiff: BailiffInfo
  +cbr_level: KycLevel
  +rosfin_hit: bool
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
  +check(ctx) CheckResult
}
class StatementVerifier {
  -strategies: list~AuthenticityStrategy~
  +check_period(statement, today) CheckResult
  +verify(statement, company) Verification
}
class AuthenticityStrategy {
  <<interface>>
  +supports(company) bool
  +verify(statement, ctx) Verification
}
PipelineOrchestrator --> DataCollector
PipelineOrchestrator --> StopFactorRule
PipelineOrchestrator --> StatementVerifier
DataCollector ..> PipelineContext : создаёт
PipelineOrchestrator ..> CheckingOutcome
StopFactorRule <|-- CompanyInactiveRule
StopFactorRule <|-- BusinessAgeUnder6mRule
StopFactorRule <|-- CurrentOverdueOver30Rule
StopFactorRule <|-- ForbiddenOkvedRule
StopFactorRule <|-- TaxBlockOrBailiffRule
StopFactorRule <|-- CbrRedLevelRule
StopFactorRule <|-- RosfinListRule
StatementVerifier --> AuthenticityStrategy
AuthenticityStrategy <|.. BankSignatureStrategy
AuthenticityStrategy <|.. FnsRevenueReconciliationStrategy
```

### Этап 1. Форма (`FormValidator`, до отправки, синхронно)

Ошибки возвращаются как `422 VALIDATION_ERROR` с `details` по полям. В конвейер такая заявка не попадает.

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

1. `DataCollector` запрашивает источники через порты, каждый обёрнут в `RetryingGateway`
   (`SOURCE_RETRY_ATTEMPTS` попыток с паузой). Результат каждого запроса сохраняется в `SourceSnapshot`.
2. Если хотя бы один источник стоп-факторов не ответил: переход в `MANUAL_REVIEW` без скоринга,
   в отчёте видно, каких данных нет (`CheckOutcome.NO_DATA`).
3. Стоп-факторы по порядку, первый сработавший даёт `REJECTED` с причиной:

| Класс | Стоп-фактор (SCOPE, раздел 4) | Источник |
|---|---|---|
| `CompanyInactiveRule` | ликвидация, банкротство, реорганизация | ЕГРЮЛ/ЕГРИП |
| `BusinessAgeUnder6mRule` | бизнесу меньше 6 месяцев | ЕГРЮЛ/ЕГРИП |
| `CurrentOverdueOver30Rule` | текущая просрочка больше 30 дней у компании, ИП, директора или учредителя от 20% | БКИ |
| `ForbiddenOkvedRule` | игорный бизнес, производство алкоголя | ЕГРЮЛ/ЕГРИП |
| `TaxBlockOrBailiffRule` | блокировка счетов ФНС или неисполненные производства ФССП | ФНС, ФССП |
| `CbrRedLevelRule` | красный уровень на платформе ЦБ «Знай своего клиента» | ЦБ |
| `RosfinListRule` | компания, директор или учредитель в перечне Росфинмониторинга | Росфинмониторинг |

4. Дозапрос (`NEEDS_INFO` + `InfoRequest`): выписка короче 6 месяцев или последняя операция старше месяца.
5. `StatementVerifier.verify` сохраняет результат проверки подлинности выписки (повод для ручной проверки применяется после скоринга):
   - `BankSignatureStrategy`: подпись банка есть и верна (мок ЭП);
   - `FnsRevenueReconciliationStrategy` (только ООО): выручка по выписке, приведённая к году, отличается от ГИР БО не больше чем на 20%.
6. Иначе переход в `SCORING`. Если `ScoringEngine` бросает `ScoringNotImplemented` (скоринг пока заглушка), заявка уходит в `MANUAL_REVIEW` с причиной `SCORING_NOT_IMPLEMENTED`, решение не вызывается (раздел 7).

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
  +apply(ctx, scoring) Decision
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
| 6 | `ClassCCounterOfferRule` | класс C | `COUNTER_OFFER`: меньше сумма, дольше срок или другой продукт |

- `RatePolicy`: A это нижняя граница диапазона варианта, B середина, C верхняя граница. Та же ставка в контрпредложении.
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
  +open_contract(app, decision, today) Contract
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
class UnitOfWork {
  <<interface>>
  +applications: ApplicationRepository
  +contracts: ContractRepository
  +users: UserRepository
  +audit: AuditLogRepository
  +commit()
  +rollback()
}
class TaskRunner {
  <<interface>>
  +enqueue(job, args)
}
class StatusPublisher {
  <<interface>>
  +publish(event) async
}
class FileStoragePort {
  <<interface>>
  +put(bucket, key, data, content_type) StoredFile
  +get(bucket, key) bytes
  +delete(bucket, key)
  +presigned_get(bucket, key, ttl_sec, file_name) str
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
  +publish(event) async
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
  #failing_inns: set~str~
  #raise_if_broken(inn)
}
ApplicationService --> UnitOfWork
ApplicationService --> TaskRunner
ApplicationService --> StatusPublisher
ApplicationService --> FileStoragePort : выписки 1С
AnalystService --> UnitOfWork
AnalystService --> StatusPublisher
SigningService --> ContractService
SigningService --> FileStoragePort : PDF договора
ContractService --> FileStoragePort
AuthService --> JwtService
CurrentUser --> JwtService
StatusPublisher <|.. WsConnectionManager
WsConnectionManager --> JwtService
UnitOfWork <|.. SqlUnitOfWork
TaskRunner <|.. AsyncioTaskRunner
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

Остальные порты: `UnitOfWork` и репозитории, `TaskRunner`, `StatusPublisher`, `FileStoragePort`, `Clock`.

### Моки и синтетика

Все внешние источники в MVP только моки. Реальных HTTP-клиентов к ЕГРЮЛ, БКИ, ФНС, ФССП, ЦБ, Росфинмониторингу
и банкам нет. Реальная интеграция позже подключается новым адаптером того же порта в `main.py`, домен и сервисы не меняются.


- Синтетика лежит в `app/infra/mocks/data/*.json`, по одному набору на ИНН. Пять компаний из SCOPE, раздел 12,
  плюс опциональные сценарии 6 (класс D) и 7 (расхождение с ФНС).
- `MockSourceAdapter` держит множество `failing_inns`, которое меняется через `/dev/mocks/failures`.
- Каждый мок делает задержку `PIPELINE_STEP_DELAY_MS`, чтобы смена статусов была видна на демо.
- `seed.py` создаёт тестовые учётки: по одному руководителю на каждую компанию и одного аналитика.
  Логины и пароли печатаются в README, а не хранятся в коде.

---

## 11. Асинхронная обработка и события

- `POST /applications/submit` синхронно делает этап 1, переводит заявку в `SUBMITTED`, коммитит,
  публикует событие и ставит `run_pipeline(application_id)` в `TaskRunner`. Ответ `202`.
- `AsyncioTaskRunner` выполняет задачи в том же процессе (`asyncio.create_task` с реестром задач и логированием ошибок).
  При старте приложения задачи по заявкам, зависшим в `SUBMITTED`, `CHECKING` или `SCORING`, перезапускаются.
- Каждый шаг конвейера это отдельная транзакция: `transition -> commit -> publish`.
- Внутри CHECKING конвейер публикует события `application.step` (какой источник проверяется), они не меняют статус.
- `ExpirationJob` раз в минуту переводит в `CANCELLED` заявки в `APPROVED` и `COUNTER_OFFER` с истёкшим `expires_at`.
  Время берётся из реального `Clock`, а не из даты договора.
- `StatusChangedEvent`: `application_id`, `owner_id`, `from_status`, `to_status`, `reason`, `is_final`, `expires_at`, `at`.
- Ограничение MVP: `WsConnectionManager` хранит подключения в памяти одного процесса.
  Для нескольких экземпляров `api` нужна шина (PostgreSQL `LISTEN/NOTIFY` или Redis), порт `StatusPublisher` это позволяет.

---

## 12. Аутентификация (JWT)

- `POST /auth/login` проверяет пароль (argon2) и выдаёт JWT HS256 на `JWT_TTL_MINUTES`. Refresh-токенов в MVP нет.
- Claims:

| Claim | Значение |
|---|---|
| `sub` | id пользователя (UUID строкой) |
| `role` | `CLIENT` или `ANALYST` |
| `name` | ФИО |
| `iat`, `exp` | время выдачи и истечения |
| `jti` | случайный id токена |

- `CurrentUser` (зависимость FastAPI) читает `Authorization: Bearer <token>`, вызывает `JwtService.decode`
  (проверка подписи, `exp`, обязательных claims) и возвращает `TokenClaims`.
  Ошибки: `401 TOKEN_MISSING`, `401 TOKEN_INVALID`, `401 TOKEN_EXPIRED`.
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
- Подписки: клиент получает события только своих заявок, аналитик получает события всех заявок.
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
- Удаление выписки удаляет объект из S3 после успешного commit.

---

## 15. Договор, операции, эмуляция

- Договор создаётся при подписании: номер, продукт и вариант, сумма или лимит, срок, ставка, график.
  `LoanContract` для кредита одной суммой, `CreditLineContract` для ВКЛ.
- Любая правка это новая `ContractVersion` (что изменилось, кто, когда, PDF). Увеличение суммы или лимита запрещено,
  только новой заявкой.
- У каждого договора своя дата `contract_date` (`ContractClock`). Эмуляция двигает только её,
  конвейер заявок живёт по реальному времени.

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
- `rewind_to(date)`: то же в цикле до даты.
- `reset`: возвращает договор в состояние сразу после выдачи (операции и версии после выдачи удаляются, PDF версии 1 остаётся).
- Денег у клиента всегда хватает, просрочки не эмулируются.

---

## 16. История и аудит

- `StatusTransition`: каждый переход заявки (кто, когда, из какого статуса, причина).
- `AuditLogRepository`: каждое действие аналитика (одобрение, контрпредложение, дозапрос, отказ, повторная проверка,
  правка договора) с обязательным комментарием.
- `ReportBuilder.build(app, audience)` собирает отчёт по заявке из 7 разделов (SCOPE, раздел 7):
  что дал клиент, что получили из источников, как сравнили, скоринг, что решили, история, договор.
  Для клиента (`audience=CLIENT`) отдаётся урезанная версия без внутренних сравнений.

---

## 17. Последовательности

### Отправка заявки и конвейер

```mermaid
sequenceDiagram
autonumber
actor C as Клиент (React)
participant WS as WS /ws/applications
participant API as applications router
participant CU as CurrentUser и JwtService
participant AS as ApplicationService
participant UOW as UnitOfWork
participant TR as TaskRunner
participant PO as PipelineOrchestrator
participant SRC as DataCollector и моки
participant SD as ScoringEngine и DecisionEngine
participant PUB as StatusPublisher (WsConnectionManager)
C->>WS: подключение, Sec-WebSocket-Protocol bearer и JWT
WS->>CU: decode(token)
CU-->>WS: TokenClaims
WS-->>C: snapshot статусов заявок пользователя
C->>API: POST /applications/submit {application_id}, Authorization Bearer JWT
API->>CU: decode(token)
CU-->>API: TokenClaims
API->>AS: submit(claims, application_id)
alt ошибки этапа 1 или чужая заявка
AS-->>API: VALIDATION_ERROR или FORBIDDEN
API-->>C: 422 или 403 ErrorOut
else форма валидна
AS->>UOW: DRAFT в SUBMITTED, commit
AS->>PUB: publish(StatusChangedEvent)
PUB-->>C: application.status_changed SUBMITTED
AS->>TR: enqueue(run_pipeline, application_id)
API-->>C: 202 application_id и status
end
TR->>PO: run(application_id)
PO->>UOW: SUBMITTED в CHECKING, commit
PO->>PUB: publish
PUB-->>C: application.status_changed CHECKING
PO->>SRC: collect(app), до 3 попыток на источник
PO->>PUB: publish step
PUB-->>C: application.step
SRC-->>PO: PipelineContext
alt источник не ответил
PO->>UOW: CHECKING в MANUAL_REVIEW
else стоп-фактор
PO->>UOW: CHECKING в REJECTED с причиной
else выписка короче 6 мес или устарела
PO->>UOW: CHECKING в NEEDS_INFO
else проверки пройдены
PO->>UOW: CHECKING в SCORING
PO->>SD: score и decide
SD-->>PO: ScoringResult и Decision
PO->>UOW: SCORING в итоговый статус
end
PO->>PUB: publish после каждого commit
PUB-->>C: application.status_changed
C->>API: POST /applications/get {application_id}
```

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
SG->>LP: open_contract(app, decision, today)
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
