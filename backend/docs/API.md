# API — контракт бэкенда для фронтенда

Архитектура описана в `docs/BACKEND_SPEC.md`, бизнес-правила в `docs/SCOPE.md`.
Если меняется что-то в этом файле, фронтенд должен узнать об этом в том же PR.

## 1. Общие правила

| Правило | Значение |
|---|---|
| Базовый путь | `/api/v1` |
| Метод | POST с JSON-телом для всех эндпоинтов с параметрами. Path- и query-параметров нет. GET только у эндпоинтов без параметров |
| Авторизация | `Authorization: Bearer <JWT>` во всех запросах, кроме `/auth/login`, `/auth/register`, `/health` |
| Пользователь | id и роль берутся только из JWT. В теле их не передают |
| Статусы заявки | приходят по WebSocket `/ws/applications`, опроса нет |
| Деньги | строка с двумя знаками: `"3600000.00"` |
| Ставки | строка: `"26.5"` |
| Даты | `"2026-10-24"`, время `"2026-10-24T12:31:00Z"` (UTC) |
| Поля | `snake_case`, перечисления строками в `UPPER_SNAKE_CASE` |
| Файлы | загрузка выписки `multipart/form-data`, скачивание по presigned-ссылке S3 на 5 минут |
| Пагинация | `page` (с 1) и `size` (по умолчанию 20, максимум 100) в теле запроса |

Ответ с ошибкой всегда такой:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Проверьте поля формы",
    "details": [{"field": "amount", "code": "TERMS_IN_RANGE", "message": "Сумма должна быть от 300 000 до 30 000 000"}]
  }
}
```

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `BAD_SIGNING_CODE` | неверный или просроченный код подписи |
| 401 | `TOKEN_MISSING` | нет заголовка Authorization |
| 401 | `TOKEN_INVALID` | подпись не сошлась или токен битый |
| 401 | `TOKEN_EXPIRED` | истёк `exp`, нужно войти заново |
| 401 | `INVALID_CREDENTIALS` | неверный логин или пароль |
| 403 | `FORBIDDEN` | роль не подходит или объект чужой |
| 403 | `NOT_DIRECTOR` | пользователь не руководитель компании |
| 404 | `NOT_FOUND` | объект не найден |
| 409 | `INVALID_STATUS` | действие недоступно в текущем статусе |
| 409 | `ACTIVE_APPLICATION_EXISTS` | по ИНН уже есть активная заявка |
| 409 | `SOURCES_NOT_READY` | аналитик одобряет, а не все источники стоп-факторов ответили |
| 410 | `OFFER_EXPIRED` | прошло 7 дней с одобрения или контрпредложения |
| 413 | `FILE_TOO_LARGE` | выписка больше 10 МБ |
| 422 | `VALIDATION_ERROR` | ошибки в теле запроса, `details` по полям |
| 503 | `SOURCE_UNAVAILABLE` | реестр не ответил |
| 503 | `STORAGE_UNAVAILABLE` | S3 не ответил, операция откатывается |
| 404 | `NOT_FOUND` | также: такого маршрута нет |
| 405 | `METHOD_NOT_ALLOWED` | маршрут есть, но не с этим методом |
| 500 | `INTERNAL_ERROR` | непредвиденная ошибка сервера, подробности только в логах |
| любой | `HTTP_ERROR` | прочие HTTP-ошибки уровня фреймворка, не перечисленные выше |

---

## 2. Эндпоинты

Колонка «Доступ»: П публично, К клиент, А аналитик, К+А оба. «Переход» показывает смену статуса заявки.

### 2.1 Аутентификация

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/auth/register` | POST | П | `{login, password, full_name, passport: {series, number, issued_at}}` | `201 UserOut` | 409 логин занят, 422 паспорт недействителен |
| `/auth/login` | POST | П | `{login, password}` | `200 LoginOut` | 401 `INVALID_CREDENTIALS` |
| `/auth/me` | GET | К+А | без параметров | `200 UserOut` | 401 |

### 2.2 Каталог и справочники

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/products` | GET | К+А | без параметров | `200 ProductOut[]` | 401 |
| `/products/recommendation` | POST | К | `{purpose}` | `200 {recommended: ProductCode, allowed: ProductCode[], explanation}` | 422 неизвестная цель |
| `/dictionaries/purposes` | GET | К+А | без параметров | `200 [{code, label, products: ProductCode[]}]` | 401 |
| `/dictionaries/collateral-types` | GET | К+А | без параметров | `200 [{code, label}]` | 401 |

### 2.3 Компании и согласия

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/companies/get` | POST | А | `{inn}` | `200 CompanyOut` | 404 ИНН не найден, 503 `SOURCE_UNAVAILABLE` |
| `/consents/give` | POST | К | `{inn, types: ConsentType[]}` | `201 ConsentOut[]` | 403 `NOT_DIRECTOR` |
| `/consents/list` | POST | К | `{inn}` | `200 ConsentOut[]` | |

Согласия на БКИ: ООО даёт `BKI_COMPANY` и `BKI_PERSONAL`, ИП даёт `BKI_PERSONAL`. Всем нужен `PERSONAL_DATA`.

### 2.4 Заявки клиента

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки и переход |
|---|---|---|---|---|---|
| `/applications/create` | POST | К | `ApplicationCreate` | `201 ApplicationOut` (DRAFT) | 409 `ACTIVE_APPLICATION_EXISTS`, 422 |
| `/applications/list` | POST | К | `{status?, page, size}` | `200 Page<ApplicationListItem>` | |
| `/applications/get` | POST | К | `{application_id}` | `200 ApplicationOut` | 403, 404 |
| `/applications/update` | POST | К | `{application_id, ...любые поля ApplicationCreate}` | `200 ApplicationOut` | 409 `INVALID_STATUS` (не DRAFT и не NEEDS_INFO) |
| `/applications/validate` | POST | К | `{application_id}` | `200 {valid, errors: [{field, code, message}]}` | |
| `/applications/statements/upload` | POST | К | `multipart/form-data`: `application_id`, `file` (1CClientBankExchange, до 10 МБ) | `201 StatementOut` | 413, 422 не формат 1С или ИНН не совпадает, 409, 503 `STORAGE_UNAVAILABLE` |
| `/applications/statements/delete` | POST | К | `{application_id, statement_id}` | `204` | 409 `INVALID_STATUS` |
| `/applications/statements/download-url` | POST | К+А | `{application_id, statement_id}` | `200 FileUrlOut` | 403, 404 |
| `/applications/submit` | POST | К | `{application_id}` | `202 {application_id, status: "SUBMITTED"}` | DRAFT или NEEDS_INFO в SUBMITTED. 422 `VALIDATION_ERROR`, 409 `ACTIVE_APPLICATION_EXISTS` |
| `/applications/history` | POST | К+А | `{application_id}` | `200 StatusTransitionOut[]` | 403 |
| `/applications/decision` | POST | К | `{application_id}` | `200 ClientDecision` | 404 решения ещё нет |
| `/applications/accept` | POST | К | `{application_id, offer_id?}` (`offer_id` обязателен для COUNTER_OFFER) | `200 ApplicationOut` | APPROVED в SIGNING. COUNTER_OFFER в SIGNING (без залога) или NEEDS_INFO (с залогом). 410 `OFFER_EXPIRED` |
| `/applications/cancel` | POST | К | `{application_id, reason?}` | `200 ApplicationOut` | в CANCELLED. 409 `INVALID_STATUS` |
| `/ws/applications` | WebSocket | К+А | JWT в `Sec-WebSocket-Protocol` | сообщения из раздела 3 | закрытие 4401, 4403 |

### 2.5 Подписание

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки и переход |
|---|---|---|---|---|---|
| `/applications/signing/code` | POST | К | `{application_id}` | `201 {code, expires_at}` (код показывается в UI) | 409 статус не SIGNING |
| `/applications/signing/confirm` | POST | К | `{application_id, code}` | `200 {status: "ISSUED", contract_id}` | SIGNING в ISSUED. 400 `BAD_SIGNING_CODE`, 503 `STORAGE_UNAVAILABLE` (выдача откатывается) |

### 2.6 Аналитик

Все действия требуют роль `ANALYST` в JWT и обязательный непустой `comment`. Действия над заявкой разрешены только в `MANUAL_REVIEW`.

| Эндпоинт | Метод | Запрос | Ответ | Ошибки и переход |
|---|---|---|---|---|
| `/analyst/applications/list` | POST | `{queue?: "manual", status?, inn?, risk_class?, page, size}` | `200 Page<AnalystListItem>` | 403 |
| `/analyst/applications/report` | POST | `{application_id}` | `200 ApplicationReport` | 404 |
| `/analyst/applications/approve` | POST | `{application_id, rate, comment}` | `200 ApplicationOut` | MANUAL_REVIEW в APPROVED. 409 `SOURCES_NOT_READY`, 422 ставка вне диапазона |
| `/analyst/applications/counter-offer` | POST | `{application_id, options: [{product_code, variant, amount, term_months, rate}], comment}` | `200 ApplicationOut` | MANUAL_REVIEW в COUNTER_OFFER |
| `/analyst/applications/request-info` | POST | `{application_id, items: [{code, message}], comment}` | `200 ApplicationOut` | MANUAL_REVIEW в NEEDS_INFO |
| `/analyst/applications/reject` | POST | `{application_id, reason_code, comment}` | `200 ApplicationOut` | MANUAL_REVIEW в REJECTED |
| `/analyst/applications/recheck` | POST | `{application_id, comment}` | `202 {application_id, status: "CHECKING"}` | MANUAL_REVIEW в CHECKING |
| `/analyst/contracts/update` | POST | `{contract_id, rate?, term_months?, comment}` | `200 ContractOut` (новая версия и новый PDF) | 422 увеличение суммы запрещено, 409 договор CLOSED |

### 2.7 Договоры

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/contracts/list` | POST | К+А | `{application_id?}` | `200 ContractListItem[]` | |
| `/contracts/get` | POST | К+А | `{contract_id}` | `200 ContractOut` | 403, 404 |
| `/contracts/schedule` | POST | К+А | `{contract_id, version_no?}` | `200 ScheduleItemOut[]` | |
| `/contracts/operations` | POST | К+А | `{contract_id, page, size}` | `200 Page<OperationOut>` | |
| `/contracts/versions` | POST | К+А | `{contract_id}` | `200 ContractVersionOut[]` | |
| `/contracts/document-url` | POST | К+А | `{contract_id, version_no?}` (по умолчанию последняя) | `200 FileUrlOut` | 403, 404 версии нет |
| `/contracts/tranches/draw` | POST | К | `{contract_id, amount}` | `201 TrancheOut` | 409 договор не ВКЛ, 422 меньше 50 000, больше доступного или окно выборки закрыто |
| `/contracts/tranches/repay` | POST | К | `{contract_id, tranche_id, amount}` | `200 ContractOut` | 422 сумма больше долга по траншу |
| `/contracts/repay` | POST | К | `{contract_id, kind: "PARTIAL" \| "FULL", amount?}` | `200 ContractOut` | FULL закрывает договор. 409 PARTIAL для ВКЛ |

### 2.8 Эмуляция (только при `DEMO_MODE=true`)

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/contracts/emulation/next-month` | POST | К+А | `{contract_id}` | `200 EmulationResult` | 409 договор CLOSED |
| `/contracts/emulation/rewind` | POST | К+А | `{contract_id, target_date}` | `200 EmulationResult` | 422 дата раньше текущей даты договора |
| `/contracts/emulation/reset` | POST | К+А | `{contract_id}` | `200 ContractOut` | |

### 2.9 Служебные

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Примечание |
|---|---|---|---|---|---|
| `/dev/mocks/failures` | POST | А (JWT обязателен) | `{source: SourceType, inn, enabled}` | `200 {source, inn, enabled}` | режим «сломаться» для демо, только при `DEMO_MODE=true` |
| `/health` | GET | П | без параметров | `200 {status: "ok", db: "ok", s3: "ok"}` | для Docker healthcheck. Если БД или S3 не отвечают: `503` с тем же телом, упавшая часть и `status` равны `"error"`, например `{status: "error", db: "ok", s3: "error"}`. Это не `ErrorOut` |

---

## 3. WebSocket `/api/v1/ws/applications`

Подключение:

```js
const ws = new WebSocket(`${WS_BASE}/api/v1/ws/applications`, ['bearer', accessToken]);
```

- Сервер проверяет JWT до принятия соединения и отвечает подпротоколом `bearer`.
- Клиент получает события своих заявок, аналитик событий всех заявок.
- Коды закрытия: `4401` токен невалиден или истёк, `4403` нет прав.
- Клиент шлёт `{"type": "ping"}` раз в 25 секунд, сервер отвечает `{"type": "pong"}`.
- После `application.status_changed` фронт перезапрашивает `POST /applications/get`.
- При разрыве: переподключиться (с новым токеном, если истёк) и взять свежий `snapshot`.

Пример последовательности после `POST /applications/submit` для сценария 1:

```json
{"type": "application.status_changed", "application_id": "…", "from_status": "DRAFT", "to_status": "SUBMITTED", "is_final": false, "at": "…"}
{"type": "application.status_changed", "application_id": "…", "from_status": "SUBMITTED", "to_status": "CHECKING", "is_final": false, "at": "…"}
{"type": "application.step", "application_id": "…", "step": "EGRUL", "label": "Проверяем данные в ЕГРЮЛ", "at": "…"}
{"type": "application.step", "application_id": "…", "step": "BKI", "label": "Проверяем кредитную историю", "at": "…"}
{"type": "application.status_changed", "application_id": "…", "from_status": "CHECKING", "to_status": "SCORING", "is_final": false, "at": "…"}
{"type": "application.status_changed", "application_id": "…", "from_status": "SCORING", "to_status": "APPROVED", "is_final": false, "expires_at": "…", "at": "…"}
```

---

## 4. Типы (TypeScript-нотация для JSDoc)

### 4.1 Перечисления

```ts
type Role = 'CLIENT' | 'ANALYST';
type CompanyKind = 'LLC' | 'IE';                 // ООО или ИП
type ProductCode = 'LUMP_SUM' | 'REVOLVING_LINE';
type Variant = 'UNSECURED' | 'SECURED';
type Purpose = 'ONE_TIME_PURCHASE' | 'DEVELOPMENT' | 'REFINANCING'
  | 'REGULAR_PURCHASES' | 'SEASONAL_GAP';
type CollateralType = 'REAL_ESTATE' | 'VEHICLE' | 'EQUIPMENT' | 'GOODS';
type ConsentType = 'BKI_COMPANY' | 'BKI_PERSONAL' | 'PERSONAL_DATA';

type ApplicationStatus = 'DRAFT' | 'SUBMITTED' | 'CHECKING' | 'NEEDS_INFO'
  | 'SCORING' | 'MANUAL_REVIEW' | 'APPROVED' | 'COUNTER_OFFER'
  | 'SIGNING' | 'ISSUED' | 'REJECTED' | 'CANCELLED';
type AllowedAction = 'EDIT' | 'UPLOAD_STATEMENT' | 'SUBMIT'
  | 'ACCEPT' | 'CANCEL' | 'SIGN';

type RiskClass = 'A' | 'B' | 'C' | 'D';
type DecisionOutcome = 'APPROVED' | 'COUNTER_OFFER' | 'NEEDS_INFO'
  | 'MANUAL_REVIEW' | 'REJECTED';
type SignatureStatus = 'VALID' | 'INVALID' | 'ABSENT';
type CheckOutcome = 'PASSED' | 'FAILED' | 'STOP_FACTOR' | 'NO_DATA';
type SourceType = 'EGRUL' | 'BKI' | 'TURNOVER' | 'FNS' | 'GIR_BO'
  | 'FSSP' | 'CBR_KYC' | 'ROSFIN' | 'BANK_SIGNATURE';

type ContractStatus = 'ACTIVE' | 'CLOSED';
type OperationType = 'DISBURSEMENT' | 'SCHEDULED_PAYMENT'
  | 'PARTIAL_PREPAYMENT' | 'FULL_REPAYMENT' | 'LIMIT_OPENED'
  | 'TRANCHE_DRAWN' | 'INTEREST_ACCRUED' | 'TRANCHE_REPAID'
  | 'CONTRACT_EDITED';

type Money = string;     // "3600000.00"
type ISODate = string;   // "2026-10-24"
type ISODateTime = string; // "2026-10-24T12:31:00Z"
```

### 4.2 Аутентификация и общие

```ts
interface UserOut {
  id: string; role: Role; full_name: string; passport_verified: boolean;
  companies: { inn: string; name: string; kind: CompanyKind; is_director: boolean }[];
}

interface LoginOut {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;        // секунды
  user: UserOut;
}

// Содержимое JWT (фронту расшифровывать не обязательно, всё есть в LoginOut)
interface TokenClaims {
  sub: string; role: Role; name: string; iat: number; exp: number; jti: string;
}

interface Page<T> { items: T[]; total: number; page: number; size: number }

interface ErrorOut {
  error: { code: string; message: string;
           details?: { field?: string; code: string; message: string }[] };
}

interface FileUrlOut {
  url: string;               // presigned GET, живёт 5 минут
  file_name: string;
  expires_at: ISODateTime;
  version_no?: number;       // для договора
}
```

### 4.3 Каталог и компания

```ts
interface ProductOut {
  code: ProductCode;
  name: string;
  purposes: Purpose[];
  variants: {
    variant: Variant;
    amount_min: Money; amount_max: Money;
    term_min: number; term_max: number;
    rate_min: string; rate_max: string;
    revenue_limit_pct: number;
    collateral_coverage_min_pct?: number;
    tranche_min?: Money;           // только REVOLVING_LINE
    tranche_term_months?: number;  // только REVOLVING_LINE
  }[];
}

interface CompanyOut {
  inn: string; kind: CompanyKind; name: string; ogrn: string;
  registered_at: ISODate;
  status: 'ACTIVE' | 'LIQUIDATED' | 'BANKRUPT' | 'REORGANIZING';
  okved: { code: string; label: string };
  director: string;
  founders: { name: string; share_pct: number }[];
  address: string;
  has_account: boolean;         // нет: нужна выписка 1С
  annual_revenue?: Money;       // по оборотам, если счёт у нас
  can_apply: boolean;
  cannot_apply_reason?: string; // "Подать может только руководитель"
}

interface ConsentOut { type: ConsentType; given_by: string; given_at: ISODateTime }
```

### 4.4 Заявка

```ts
interface ApplicationCreate {
  inn: string;
  purpose: Purpose;
  product_code: ProductCode;
  variant: Variant;
  amount: Money;
  term_months: number;
  collateral?: { type: CollateralType; description: string; estimated_value: Money };
  owner_guarantee_consent?: boolean;   // ООО без залога
}

interface StatementOut {
  id: string;
  file_name: string;            // оригинал в S3, ссылка через statements/download-url
  bank_name: string;
  account_inn: string;
  inn_matches: boolean;
  period_from: ISODate;
  period_to: ISODate;
  months_covered: number;       // нужно 6 и больше
  last_operation_at: ISODate;   // не старше месяца
  operations_count: number;
  signature_status: SignatureStatus;
  annual_revenue: Money;        // приведено к году
}

interface ApplicationOut {
  id: string;
  inn: string;
  company: { name: string; kind: CompanyKind; okved: string };
  purpose: Purpose;
  product_code: ProductCode;
  variant: Variant;
  amount: Money;
  term_months: number;
  collateral?: { type: CollateralType; description: string; estimated_value: Money };
  owner_guarantee_consent?: boolean;
  statements: StatementOut[];
  status: ApplicationStatus;
  status_label: string;          // "Контрпредложение"
  info_request?: { items: { code: string; message: string }[];
                   message: string; created_at: ISODateTime };
  decision?: ClientDecision;
  expires_at?: ISODateTime;      // 7 дней для APPROVED и COUNTER_OFFER
  allowed_actions: AllowedAction[];
  contract_id?: string;          // после ISSUED
  created_at: ISODateTime; updated_at: ISODateTime;
}

interface ApplicationListItem {
  id: string; inn: string; company_name: string;
  product_code: ProductCode; variant: Variant; amount: Money;
  status: ApplicationStatus; updated_at: ISODateTime;
}

interface ClientDecision {
  outcome: DecisionOutcome;
  reasons: string[];             // ["Сумма выше лимита от выручки без залога"]
  key_factors: { label: string; points: number }[];
  scoring?: { total: number; risk_class: RiskClass; factors: FactorScore[] };
  rate?: string;
  offers: OfferOption[];
  expires_at?: ISODateTime;
}

interface OfferOption {
  id: string; product_code: ProductCode; variant: Variant;
  amount: Money; term_months: number; rate: string;
}

interface FactorScore { code: string; label: string; value: string; points: number }

interface StatusTransitionOut {
  from_status: ApplicationStatus | null;
  to_status: ApplicationStatus;
  actor: { id: string | null; role: Role | 'SYSTEM'; name: string };
  reason?: string;
  at: ISODateTime;
}
```

Пример `ClientDecision` для сценария 3 (ИП Соколов):

```json
{
  "outcome": "COUNTER_OFFER",
  "reasons": ["Запрошенная сумма выше лимита от выручки для варианта без залога"],
  "key_factors": [{"label": "Возраст бизнеса", "points": 20}, {"label": "Кредитная история", "points": 20}],
  "scoring": {"total": 55, "risk_class": "B", "factors": []},
  "rate": "26.5",
  "offers": [
    {"id": "…", "product_code": "LUMP_SUM", "variant": "UNSECURED", "amount": "3600000.00", "term_months": 24, "rate": "26.5"},
    {"id": "…", "product_code": "LUMP_SUM", "variant": "SECURED", "amount": "4500000.00", "term_months": 24, "rate": "26.5"}
  ],
  "expires_at": "…"
}
```

### 4.5 Аналитик

```ts
interface AnalystListItem {
  id: string; inn: string; company_name: string;
  product_code: ProductCode; variant: Variant; amount: Money;
  status: ApplicationStatus; risk_class?: RiskClass;
  manual_reasons: string[]; submitted_at: ISODateTime;
}

interface ApplicationReport {
  application: ApplicationOut;
  provided: {                         // 1. что дал клиент
    applicant: { full_name: string; passport_verified: boolean };
    consents: ConsentOut[];
    collateral?: { type: CollateralType; description: string; estimated_value: Money };
    owner_guarantee_consent?: boolean;
    statements: StatementOut[];
  };
  sources: {                          // 2. что получили сами
    source: SourceType;
    status: 'OK' | 'FAILED';
    attempts: number;
    fetched_at?: ISODateTime;
    data?: Record<string, unknown>;
  }[];
  comparisons: {                      // 3. как сравнили
    code: string; title: string; stage: 'FORM' | 'CHECKING' | 'POST_SCORING';
    expected: string; actual: string; outcome: CheckOutcome;
  }[];
  turnover: { month: string; incoming: Money; outgoing: Money }[];
  top_payers: { inn: string; name: string; share_pct: number }[];
  scoring?: {                         // 4. скоринг
    total: number; risk_class: RiskClass; debt_load_pct: number;
    factors: FactorScore[];
  };
  decision?: {                        // 5. что решили
    rule_code: string; outcome: DecisionOutcome;
    manual_reasons: string[];
    system_rate?: string;
    analyst?: { id: string; name: string; rate?: string;
                comment: string; decided_at: ISODateTime };
  };
  history: StatusTransitionOut[];     // 6. история
  contract?: { id: string; number: string; versions_count: number }; // 7. после выдачи
}
```

### 4.6 Договор

```ts
interface ContractListItem {
  id: string; number: string; product_code: ProductCode; variant: Variant;
  status: ContractStatus; outstanding: Money; contract_date: ISODate;
}

interface ContractOut {
  id: string;
  number: string;                 // "КД-2026-000123"
  application_id: string;
  product_code: ProductCode;
  variant: Variant;
  status: ContractStatus;
  contract_date: ISODate;         // своя дата договора (эмуляция)
  rate: string;
  term_months: number;
  version_no: number;
  document?: { version_no: number; file_name: string };  // PDF в S3, ссылка через /contracts/document-url
  outstanding: Money;             // остаток долга
  next_payment?: { date: ISODate; amount: Money; principal: Money; interest: Money };
  principal?: Money;              // только LUMP_SUM
  limit?: Money;                  // только REVOLVING_LINE
  available?: Money;              // только REVOLVING_LINE
  draw_until?: ISODate;           // окно выборки траншей
  tranches?: TrancheOut[];
  allowed_operations: ('DRAW_TRANCHE' | 'REPAY_TRANCHE' | 'PARTIAL_PREPAYMENT' | 'FULL_REPAYMENT')[];
}

interface TrancheOut {
  id: string; amount: Money; issued_at: ISODate; due_at: ISODate; outstanding: Money;
}

interface ScheduleItemOut {
  n: number; due_date: ISODate; payment: Money; principal: Money; interest: Money; balance: Money;
}

interface OperationOut {
  id: string; type: OperationType; amount: Money;
  contract_date: ISODate; actor: 'SYSTEM' | 'EMULATOR' | string;
  balance_after: Money; tranche_id?: string;
}

interface ContractVersionOut {
  version_no: number;
  changes: { field: string; old: string; new: string }[];
  author: { id: string | null; role: Role | 'SYSTEM'; name: string };
  reason?: string;
  created_at: ISODateTime;
  has_document: boolean;
}

interface EmulationResult {
  contract_date: ISODate;
  operations: OperationOut[];     // что произошло за сдвиг
  contract: ContractOut;
}
```

### 4.7 WebSocket

```ts
type WsServerMessage =
  | { type: 'snapshot';
      applications: { application_id: string; status: ApplicationStatus; updated_at: ISODateTime }[] }
  | { type: 'application.status_changed';
      application_id: string;
      from_status: ApplicationStatus | null;
      to_status: ApplicationStatus;
      reason?: string;
      is_final: boolean;               // ISSUED, REJECTED, CANCELLED
      expires_at?: ISODateTime;        // для APPROVED и COUNTER_OFFER
      at: ISODateTime }
  | { type: 'application.step';        // шаги внутри CHECKING, статус не меняют
      application_id: string;
      step: 'EGRUL' | 'BKI' | 'TURNOVER' | 'FNS' | 'FSSP' | 'CBR_KYC' | 'ROSFIN' | 'STATEMENT' | 'SCORING';
      label: string;                   // "Проверяем кредитную историю"
      at: ISODateTime }
  | { type: 'pong' };

type WsClientMessage = { type: 'ping' };
```

---

## 5. Временные ограничения

- Модель скоринга пока не реализована (её добавит коллега). До этого конвейер после проверок переводит заявку
  в `MANUAL_REVIEW` с причиной `SCORING_NOT_IMPLEMENTED` в `manual_reasons`, поле `scoring` в `ClientDecision`
  и `ApplicationReport` отсутствует, решение принимает аналитик. Фронт должен корректно показывать заявку без скоринга.
  Когда модель появится, контракт не изменится: поле `scoring` просто начнёт приходить.
- Все внешние источники (ЕГРЮЛ, БКИ, ФНС, ФССП, ЦБ, Росфинмониторинг, ЭП банка) это моки с синтетикой.
  Для демо есть пять тестовых компаний из SCOPE раздел 12.

## 6. Открытые вопросы к команде

- `/companies/get` сейчас доступен только аналитику. Анкете клиента нужны данные компании для автоподтягивания
  (SCOPE, раздел 3). Варианты: открыть эндпоинт и клиенту (только для ИНН, где он руководитель),
  либо отдавать компанию внутри `ApplicationOut.company` после `create`. Решить до этапа 5.
