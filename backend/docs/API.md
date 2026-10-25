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
| Числа во входных телах | деньги и ставки в запросе принимаются строкой или числом, в ответах всегда строка. Строка надёжнее: число в JS теряет копейки |
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
| 403 | `NOT_DIRECTOR` | пользователь не руководитель компании, не сам ИП и не представитель с подтверждённой доверенностью |
| 409 | `LOGIN_TAKEN` | при регистрации: логин уже занят (без учёта регистра) |
| 409 | `INN_TAKEN` | при регистрации: аккаунт с этим ИНН физлица уже есть |
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
| `/auth/register` | POST | П | `{login, password, full_name, inn, passport: {series, number, issued_at}}`, `inn` — 12 цифр ИНН физлица | `201 UserOut` | 409 `LOGIN_TAKEN`, 409 `INN_TAKEN`, 422 паспорт недействителен (`details[].code = PASSPORT_INVALID`), 422 неверные контрольные разряды ИНН (`details[].code = INN_INVALID`) |
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
| `/companies/get` | POST | К+А | `{inn}` | `200 CompanyOut` | 404 ИНН не найден, 503 `SOURCE_UNAVAILABLE` |
| `/consents/give` | POST | К | `{inn, types: ConsentType[]}` | `201 ConsentOut[]` | 403 `NOT_DIRECTOR` |
| `/consents/list` | POST | К | `{inn}` | `200 ConsentOut[]` | |

Согласия на БКИ: ООО даёт `BKI_COMPANY` и `BKI_PERSONAL`, ИП даёт `BKI_PERSONAL`. Всем нужен `PERSONAL_DATA`.
Неподходящий для формы бизнеса тип (`BKI_COMPANY` у ИП) даёт `422`, `details[].code = CONSENT_NOT_APPLICABLE`.

Регистрация и вход:

- Логин сравнивается без учёта регистра и хранится в нижнем регистре: `3–64` символа из `A-Z a-z 0-9 . _ @ -`.
  Пароль `8–128` символов. Паспорт: `series` 4 цифры, `number` 6 цифр, `issued_at` не позже сегодняшнего дня.
- Лишние поля в теле любого запроса отклоняются `422` (например, `role` в `/auth/register`): роль при регистрации всегда `CLIENT`.

Компании и согласия:

- `/companies/get` отдаёт данные ЕГРЮЛ/ЕГРИП любому клиенту: это открытый реестр, анкете они нужны для автоподтягивания
  (SCOPE, раздел 3). Банковские данные (`has_account`, `annual_revenue`) приходят только руководителю компании и аналитику.
  `can_apply` у клиента это «руководитель по ЕГРЮЛ или сам ИП», иначе `cannot_apply_reason`.
  У аналитика `can_apply = false` и причина «Заявку подаёт клиент».
- Согласия даются по ИНН до создания заявки и хранятся по паре «ИНН, пользователь». Повторное согласие того же типа
  не меняет `given_at`. После первого `/consents/give` компания появляется в `UserOut.companies`.

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
| `/applications/findings` | POST | К | `{application_id}` | `200 ApplicationFindings` | 403 чужая заявка, 404, 409 `INVALID_STATUS` проверка по источникам ещё идёт (`SUBMITTED`, `CHECKING`) или не запускалась |
| `/applications/accept` | POST | К | `{application_id, offer_id?}` (`offer_id` обязателен для COUNTER_OFFER) | `200 ApplicationOut` | APPROVED в SIGNING. COUNTER_OFFER в SIGNING (без залога) или NEEDS_INFO (с залогом). 410 `OFFER_EXPIRED` |
| `/applications/cancel` | POST | К | `{application_id, reason?}` | `200 ApplicationOut` | в CANCELLED. 409 `INVALID_STATUS` |
| `/ws/applications` | WebSocket | К+А | JWT в `Sec-WebSocket-Protocol` | сообщения из раздела 3 | закрытие 4401, 4403 |

`/applications/findings` показывает клиенту, что банк нашёл по источникам: данные компании из ЕГРЮЛ/ЕГРИП,
кредитную нагрузку по БКИ и выручку. Проверок, сравнений, скоринга и решений банка в нём нет. Раздел источника,
который не ответил, просто отсутствует. Что не так с заявкой, клиенту пишет аналитик через дозапрос
(`info_request.items[].message`). Ответ строится из сохранённых данных, повторного запроса к источникам нет.

### 2.5 Подписание

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки и переход |
|---|---|---|---|---|---|
| `/applications/signing/code` | POST | К | `{application_id}` | `201 {code, expires_at}` (код показывается в UI) | 409 статус не SIGNING, 403 чужая заявка |
| `/applications/signing/confirm` | POST | К | `{application_id, code}` | `200 {status: "ISSUED", contract_id}` | SIGNING в ISSUED. 400 `BAD_SIGNING_CODE`, 409 статус не SIGNING, 503 `STORAGE_UNAVAILABLE` (выдача откатывается) |

Код это 6 цифр, живёт 5 минут (`SIGNING_CODE_TTL_SECONDS`). Повторный запрос кода заменяет прежний.
Неверный и просроченный код одинаково дают 400 `BAD_SIGNING_CODE`. После 503 заявка остаётся в `SIGNING`,
договора нет, а код остаётся действительным: подтверждение можно повторить. При успехе клиенту приходит
`application.status_changed` в `ISSUED`, в `ApplicationOut` появляется `contract_id`.

### 2.6 Аналитик

Все действия требуют роль `ANALYST` в JWT и обязательный непустой `comment` (до 2000 символов, строка из пробелов
считается пустой и даёт 422 с `details[].field = "comment"`). Действия над заявкой разрешены только в `MANUAL_REVIEW`,
в других статусах 409 `INVALID_STATUS`. Каждое действие пишется в аудит-лог вместе с комментарием.
Комментарий внутренний: клиент его не видит, в истории статусов у перехода короткий текст вроде «Одобрено аналитиком»,
а сам комментарий виден в отчёте (`decision.analyst.comment`).

- Список без `queue` показывает все заявки, кроме черновиков, от новых к старым по `updated_at`.
  Заявка видна с момента `SUBMITTED`, а отчёт по ней открывается, когда worker обошёл все источники
  (статус уже не `SUBMITTED` и не `CHECKING`). До этого `report` даёт 409 `INVALID_STATUS`.
- Если источник не ответил, отчёт всё равно собирается по остальным данным. У такого источника в `sources`
  `status: "FAILED"`, `attempts: 3`, `data: {error: "Нет ответа после 3 попыток"}`. Если не ответил ЕГРЮЛ,
  запросы, которым нужны его данные (БКИ и ФССП директора и учредителей, Росфинмониторинг), не делаются: у них
  `status: "SKIPPED"`, `attempts: 0`, `data: {error: "Не запрашивали: нет ответа ЕГРЮЛ"}`. Сравнения считаются
  по имеющимся данным, у правил без данных `outcome: "NO_DATA"` и `actual: "нет ответа"`. Заявка в этом случае
  всегда в `MANUAL_REVIEW` с `SOURCE_UNAVAILABLE`, автоотказа нет, даже если стоп-фактор виден.
  `queue: "manual"` оставляет только `MANUAL_REVIEW`, старые первыми. Фильтры складываются через «и».
- Решение аналитика сохраняется с `rule_code = "ANALYST_DECISION"`, поэтому клиентский `ClientDecision` после него
  показывает исход аналитика, а отчёт показывает и решение системы, и решение аналитика.
- `counter-offer`: от 1 до 5 вариантов, каждый проверяется по границам продукта и варианта и по цели заявки.
  Ошибки приходят по всем вариантам сразу, с полями `options[i].amount`, `options[i].term_months`, `options[i].rate`,
  `options[i].product_code`. Вариант с залогом, принятый клиентом, переводит заявку в `NEEDS_INFO` с пунктом `COLLATERAL`.
- `request-info`: от 1 до 10 пунктов, `code` в `UPPER_SNAKE_CASE` (до 64 символов), `message` это текст для клиента
  (до 500 символов). Клиент видит его в `ApplicationOut.info_request.items[].message`.
- `reject`: `reason_code` в `UPPER_SNAKE_CASE`, закрытого списка нет. Удобно брать коды из раздела 4.8.

| Эндпоинт | Метод | Запрос | Ответ | Ошибки и переход |
|---|---|---|---|---|
| `/analyst/applications/list` | POST | `{queue?: "manual", status?, inn?, risk_class?, page, size}` | `200 Page<AnalystListItem>` | 403 |
| `/analyst/applications/report` | POST | `{application_id}` | `200 ApplicationReport` | 404, 409 `INVALID_STATUS` проверка по источникам ещё идёт (`SUBMITTED`, `CHECKING`) |
| `/analyst/applications/approve` | POST | `{application_id, rate, comment}` | `200 ApplicationOut` | MANUAL_REVIEW в APPROVED. 409 `SOURCES_NOT_READY` (есть источник со статусом `FAILED`, нужна повторная проверка), 422 ставка вне диапазона варианта (`RATE_OUT_OF_RANGE`) |
| `/analyst/applications/counter-offer` | POST | `{application_id, options: [{product_code, variant, amount, term_months, rate}], comment}` | `200 ApplicationOut` | MANUAL_REVIEW в COUNTER_OFFER. 422 вариант вне границ продукта |
| `/analyst/applications/request-info` | POST | `{application_id, items: [{code, message}], comment}` | `200 ApplicationOut` | MANUAL_REVIEW в NEEDS_INFO |
| `/analyst/applications/reject` | POST | `{application_id, reason_code, comment}` | `200 ApplicationOut` | MANUAL_REVIEW в REJECTED |
| `/analyst/applications/recheck` | POST | `{application_id, comment}` | `202 {application_id, status: "CHECKING"}` | MANUAL_REVIEW в CHECKING |
| `/analyst/contracts/update` | POST | `{contract_id, rate?, term_months?, comment}` | `200 ContractOut` (новая версия и новый PDF) | 422 `VALIDATION_ERROR`: нет ни `rate`, ни `term_months` (`details[].field = "rate"`, `REQUIRED`), ставка вне диапазона варианта (`RATE_OUT_OF_RANGE`), срок вне границ варианта (`TERMS_IN_RANGE`), условия не изменились (`NO_CHANGES`), пустой комментарий; поля `amount` или `limit` в запросе нет, поэтому попытка их передать тоже даёт 422 (лишние поля запрещены). 409 договор CLOSED, 404, 503 `STORAGE_UNAVAILABLE` (правка откатывается) |

### 2.7 Договоры

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/contracts/list` | POST | К+А | `{application_id?}` | `200 ContractListItem[]` (клиенту только свои, новые сначала) | |
| `/contracts/get` | POST | К+А | `{contract_id}` | `200 ContractOut` | 403, 404 |
| `/contracts/schedule` | POST | К+А | `{contract_id, version_no?}` (по умолчанию последняя) | `200 ScheduleItemOut[]` (у ВКЛ пустой список) | 403, 404 версии нет |
| `/contracts/operations` | POST | К+А | `{contract_id, page, size}` | `200 Page<OperationOut>` | |
| `/contracts/versions` | POST | К+А | `{contract_id}` | `200 ContractVersionOut[]` | |
| `/contracts/document-url` | POST | К+А | `{contract_id, version_no?}` (по умолчанию последняя) | `200 FileUrlOut` | 403, 404 версии нет |
| `/contracts/tranches/draw` | POST | К | `{contract_id, amount}` | `201 TrancheOut` | 409 договор не ВКЛ или закрыт, 422 с `details[].code`: `TRANCHE_BELOW_MIN` (меньше 50 000), `OVER_AVAILABLE` (больше доступного), `DRAW_WINDOW_CLOSED` (окно выборки закрыто) |
| `/contracts/tranches/repay` | POST | К | `{contract_id, tranche_id, amount}` | `200 ContractOut` | 422 `OVER_TRANCHE_DEBT` сумма больше долга по траншу, 404 транша нет, 409 договор не ВКЛ или закрыт |
| `/contracts/repay` | POST | К | `{contract_id, kind: "PARTIAL" \| "FULL", amount?}` | `200 ContractOut` | FULL закрывает договор, `amount` не нужен. PARTIAL: `amount` обязателен (`REQUIRED`) и меньше долга (`AMOUNT_NOT_LESS_THAN_DEBT`), создаёт новую версию договора с PDF. 409 PARTIAL для ВКЛ или договор закрыт, 503 `STORAGE_UNAVAILABLE` (погашение откатывается) |

Операции проводит только клиент по своему договору: аналитику и чужому клиенту 403.
Транш берётся датой договора (`contract_date`) на 6 месяцев (`tranche_term_months`).

### 2.8 Эмуляция (только при `DEMO_MODE=true`)

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Ошибки |
|---|---|---|---|---|---|
| `/contracts/emulation/next-month` | POST | К+А | `{contract_id}` | `200 EmulationResult` | 409 договор CLOSED |
| `/contracts/emulation/rewind` | POST | К+А | `{contract_id, target_date}` | `200 EmulationResult` | 422 `details[].field = "target_date"`: `DATE_IN_PAST` (раньше текущей даты договора), `DATE_AFTER_END` (позже окончания договора). 409 договор CLOSED |
| `/contracts/emulation/reset` | POST | К+А | `{contract_id}` | `200 ContractOut` | |

Без `DEMO_MODE=true` все три дают `404 NOT_FOUND`. Клиент работает только со своим договором (иначе 403).
Месяц отсчитывается от даты подписания: договор от 31 января после сдвигов показывает 28 февраля, затем 31 марта.
`reset` удаляет операции и версии после выдачи (PDF версии 1 остаётся) и снова открывает закрытый договор.

### 2.9 Служебные

| Эндпоинт | Метод | Доступ | Запрос | Ответ | Примечание |
|---|---|---|---|---|---|
| `/dev/mocks/failures` | POST | А (JWT обязателен) | `{source: SourceType, inn, enabled}` | `200 {source, inn, enabled}` | режим «сломаться» для демо, только при `DEMO_MODE=true`, иначе `404 NOT_FOUND` |
| `/health` | GET | П | без параметров | `200 {status: "ok", db: "ok", s3: "ok"}` | для Docker healthcheck. Если БД или S3 не отвечают: `503` с тем же телом, упавшая часть и `status` равны `"error"`, например `{status: "error", db: "ok", s3: "error"}`. Это не `ErrorOut` |

---

## 3. WebSocket `/api/v1/ws/applications`

Подключение:

```js
const ws = new WebSocket(`${WS_BASE}/api/v1/ws/applications`, ['bearer', accessToken]);
```

- Сервер проверяет JWT до принятия соединения и отвечает подпротоколом `bearer`.
- Клиент получает события своих заявок, аналитик событий всех заявок.
- Коды закрытия: `4401` токен невалиден или истёк (соединение сначала принимается, затем закрывается с этим кодом),
  `4403` нет прав (сейчас не используется).
- Шаги `application.step` приходят между `CHECKING` и следующим статусом: `EGRUL, BKI, TURNOVER, FNS, FSSP, CBR_KYC, ROSFIN`,
  затем `STATEMENT`, если загружена выписка. Шаг `SCORING` зарезервирован и сейчас не отправляется.
  Шаги приходят и тогда, когда источник не ответил или запрос пропущен: что получилось, видно в отчёте аналитика.
- События идут от worker через RabbitMQ, поэтому между `submit` (ответ 202) и `CHECKING` может пройти
  до нескольких секунд, а если очередь была недоступна, до `PIPELINE_STALE_AFTER_SEC` (по умолчанию 2 минуты).
- `reason` у системных переходов это машинный код (раздел 4.8), у действий клиента текст.
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
type FetchStatus = 'OK' | 'FAILED' | 'SKIPPED';   // FAILED: нет ответа после всех попыток;
                                                  // SKIPPED: не запрашивали, нет ответа ЕГРЮЛ

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
  id: string; role: Role; full_name: string; inn: string | null; passport_verified: boolean;
  // Компании, по которым пользователь давал согласия (/consents/give). У seed-учёток это их компания.
  // basis: основание полномочий (SOLE_TRADER | DIRECTOR | POWER_OF_ATTORNEY), null у привязок до этой версии
  companies: { inn: string; name: string; kind: CompanyKind; is_director: boolean; basis: AuthorityBasis | null }[];
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
           // field есть всегда: путь к полю, например "options[0].rate", или "body" для ошибки всего тела
           details?: { field: string; code: string; message: string }[] };
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
  has_account?: boolean;        // false: нужна выписка 1С. Только руководителю и аналитику
  annual_revenue?: Money;       // по оборотам за 12 мес, если счёт у нас. Только руководителю и аналитику
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
  last_operation_at?: ISODate;  // не старше месяца; нет, если в выписке нет операций
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
  info_request?: { items: { code: string; message: string }[];   // message: текст аналитика или подпись по коду
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

interface ApplicationFindings {
  application_id: string;
  collected_at: ISODateTime;           // когда worker закончил сбор
  company?: {                          // ЕГРЮЛ/ЕГРИП; нет, если реестр не ответил
    inn: string; kind: CompanyKind; name: string; ogrn: string;   // поля как в CompanyOut
    registered_at: ISODate;
    status: 'ACTIVE' | 'LIQUIDATED' | 'BANKRUPT' | 'REORGANIZING';
    okved: { code: string; label: string };
    address: string;
    director: string;
    founders: { full_name: string; share_pct: number }[];   // у ИП пустой
  };
  credit_load?: {                      // БКИ по компании или ИП; нет, если БКИ не ответило
    has_history: boolean;
    monthly_payments: Money;           // текущие платежи по кредитам в месяц
  };
  revenue?: {                          // нет, если ни одного раздела нет
    account_turnover?: {               // обороты по счёту в нашем банке; нет, если счёта нет или источник не ответил
      months: { month: string; incoming: Money }[];   // month "2026-09"
      total_incoming: Money;
    };
    annual_report?: { year: number; revenue: Money };  // ГИР БО за прошлый год, только у ООО
  };
}

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
    {"id": "…", "product_code": "LUMP_SUM", "variant": "SECURED", "amount": "4500000.00", "term_months": 24, "rate": "24.5"}
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
  sources: {                          // 2. что получили сами, по снимку на каждый запрос
    source: SourceType;               // BKI повторяется: компания, затем руководитель и учредители
    status: FetchStatus;
    attempts: number;                 // у SKIPPED 0
    fetched_at?: ISODateTime;
    data?: Record<string, unknown>;   // ответ источника; деньги строками, даты ISO;
                                      // скалярный ответ как {value}; у FAILED и SKIPPED {error}
  }[];
  comparisons: {                      // 3. как сравнили
    code: string; title: string; stage: 'FORM' | 'CHECKING' | 'POST_SCORING';
    expected: string; actual: string; outcome: CheckOutcome;
  }[];
  turnover: { month: string; incoming: Money; outgoing: Money }[];   // month "2026-09"; счёт у нас, иначе выписка
  top_payers: { inn: string; name: string; share_pct: number }[];   // до 5, доля за весь период;
                                                                    // по выписке name пустой
  scoring?: {                         // 4. скоринг
    total: number; risk_class: RiskClass; debt_load_pct: number;
    factors: FactorScore[];
  };
  decision?: {                        // 5. что решили
    rule_code: string; outcome: DecisionOutcome;
    manual_reasons: string[];
    system_rate?: string;
    analyst?: { id: string; name: string; rate?: string;   // решение аналитика после этого решения системы
                comment: string; decided_at: ISODateTime };
  };                                  // rule_code, outcome, manual_reasons, system_rate: последнее решение системы
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
  contract_date: ISODate; actor: 'SYSTEM' | 'EMULATOR' | string;  // string это id пользователя
  // EMULATOR: плановый платёж, проценты и гашение транша в конце срока, проведённые эмуляцией
  balance_after: Money; tranche_id?: string;
}

interface ContractVersionOut {
  version_no: number;
  // field: "rate" или "term_months" (правка аналитиком), "outstanding" (частичное досрочное погашение,
  // reason "Частичное досрочное погашение"). У версии 1 пусто, автор это подписавший клиент, reason "Договор подписан".
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

### 4.8 Коды причин

`manual_reasons`: `SECURED_VARIANT`, `STATEMENT_UNVERIFIED`, `AMOUNT_OVER_5M`, `RECENT_OWNER_CHANGE`,
`UNRELIABLE_REGISTRY_RECORD`, `CBR_YELLOW_LEVEL`, `TAX_DEBT_OVER_10K`, `DIRECTOR_BAILIFF_OVER_10K`,
`NO_COUNTER_OFFER_AVAILABLE`, `SCORING_NOT_IMPLEMENTED`, `SOURCE_UNAVAILABLE` (источник не ответил на этапе CHECKING).

Заголовки строк `comparisons[].title` формирует бэкенд, фронт показывает их как есть.

Стоп-факторы (`comparisons[].code` с `outcome: "STOP_FACTOR"`): `COMPANY_INACTIVE`, `BUSINESS_AGE_UNDER_6M`,
`CURRENT_OVERDUE_OVER_30`, `FORBIDDEN_OKVED`, `TAX_BLOCK_OR_BAILIFF`, `CBR_RED_LEVEL`, `ROSFIN_LIST`, `DIRECTOR_DISQUALIFIED`.

`decision.rule_code`: `MANUAL_REVIEW`, `CLASS_D_REJECT`, `REVENUE_LIMIT_COUNTER_OFFER`, `CLASS_A_APPROVE`,
`CLASS_B_APPROVE`, `CLASS_C_COUNTER_OFFER`, `STOP_FACTOR` (отказ на этапе CHECKING, в `reasons` код стоп-фактора),
`ANALYST_DECISION` (решение аналитика после ручной проверки; при отказе в `reasons` его `reason_code`).

`reason` в `application.status_changed` и истории для системных переходов: `PIPELINE_STARTED`, `CHECKS_PASSED`,
`SOURCE_UNAVAILABLE` (источник не ответил, ручная проверка), код стоп-фактора, `SCORING_NOT_IMPLEMENTED`,
`PIPELINE_FAILED` (прогон конвейера падал на каждой доставке, ручная проверка), `STATEMENT_PERIOD`, причины решения через запятую или `rule_code`, `OFFER_EXPIRED` (прошло 7 дней).

В контрпредложении у каждого варианта своя ставка по классу риска (`offers[].rate`).

## 5. Временные ограничения

- Модель скоринга пока не реализована (её добавит коллега). До этого конвейер после проверок переводит заявку
  в `MANUAL_REVIEW` с причиной `SCORING_NOT_IMPLEMENTED` в `manual_reasons`, поле `scoring` в `ClientDecision`
  и `ApplicationReport` отсутствует, решение принимает аналитик. Фронт должен корректно показывать заявку без скоринга.
  Когда модель появится, контракт не изменится: поле `scoring` просто начнёт приходить.
- Все внешние источники (ЕГРЮЛ, БКИ, ФНС, ФССП, ЦБ, Росфинмониторинг, ЭП банка) это моки с синтетикой.
  Для демо есть пять тестовых компаний из SCOPE раздел 12.

## 6. Открытые вопросы к команде

Открытых вопросов нет.

Решено на этапе 4: `/companies/get` открыт клиенту. ЕГРЮЛ видит любой клиент, банковские данные
только руководитель и аналитик (см. раздел 2.3).
