# Фронтенд кредитного кабинета

React 18 + Vite, без UI-библиотек: компоненты и токены перенесены из дизайн-системы «Кредитный кабинет» (макеты Claude Design, `Credit Cabinet.dc.html`). Только светлая тема.

## Запуск

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev            # http://localhost:5173
```

Фронтенд работает только с бэкендом: без него на экране входа будет «Нет связи с сервером». Список тестовых учёток на экране входа — статичный текст из макета (`bakery`, `romashka`, `sokolov`, `ivanova`, `techprom`, `analyst`, пароль `demo`), бэкенду нужно завести такие же.

## Подключение бэкенда

| Переменная | Что делает |
|---|---|
| `VITE_API_URL` | База запросов, по умолчанию `/api` |
| `VITE_BACKEND_URL` | Куда dev-сервер проксирует `/api` (префикс `/api` срезается). По умолчанию `http://localhost:8000` |
| `VITE_WS_URL` | Адрес вебсокетов, если отличается от API |

Все запросы — в одном файле [src/api/real.js](src/api/real.js). Если путь или поле на бэкенде называется иначе, поправить нужно только его.

- Авторизация: `Authorization: Bearer <access_token>` из `POST /auth/login`. Для вебсокета токен передаётся в `?token=`.
- Ошибки: тело `{ "detail": "текст для человека" }`. Ошибки полей — `{ "errors": [{ "field": "amount", "message": "…" }] }` (или стандартный `detail[]` FastAPI с `loc`). Поля с точкой: `collateral.estimated_value`.
- 401 — выход на экран входа, 0 или 5xx — полоса «Нет связи с сервером» с кнопкой «Повторить».

### Что ждёт фронтенд

| Запрос | Ответ (главные поля) |
|---|---|
| `POST /auth/login` | `{ access_token, user }` |
| `GET /auth/me` | `{ id, full_name, role: CLIENT \| ANALYST, companies: [{ inn, name, has_account }] }` |
| `POST /auth/register` | 201; 409 — логин занят; 422 — паспорт недействителен (`errors[].field = passport`) |
| `GET /dictionaries/purposes`, `/collateral-types`, `/reject-reasons` | `[{ code, label }]` |
| `GET /products` | `[{ code: LOAN \| CREDIT_LINE, name, tranche_min?, variants: [{ code: UNSECURED \| SECURED, name, amount_min, amount_max, term_min, term_max, rate_min, collateral_required }] }]` |
| `GET /products/recommendation?purpose=` | `{ recommended, allowed[], title, explanation }` |
| `GET /applications?inn=` | `{ items: Application[], total }` |
| `GET /applications/{id}` | `Application` (ниже) |
| `POST /applications`, `PATCH /applications/{id}` | `Application`; 409 — активная заявка по ИНН, `{ detail, application_id }` |
| `POST /applications/{id}/validate` | `{ errors: [{ field, message }] }` |
| `POST /applications/{id}/submit` | 202; 422 — `errors[]` |
| `POST /applications/{id}/statements` (multipart `file`) | `Statement`; 422 — выписка другой компании |
| `DELETE /applications/{id}/statements/{sid}` | 204 |
| `GET /applications/{id}/history` | `[{ at, from_status, to_status, actor: { name } \| null, reason }]` |
| `POST /applications/{id}/accept` `{ offer_id }` | 200; 410 — срок предложения истёк |
| `POST /applications/{id}/cancel` | 200 |
| `POST /applications/{id}/signing/code` | `{ code, expires_at }` |
| `POST /applications/{id}/signing/confirm` `{ code }` | `{ status: ISSUED, contract_id }`; 400 — неверный код |
| `WS /applications/{id}/status` | сообщения `{ status, step_label, expires_at }` |
| `GET /consents?inn=` / `POST /consents` `{ inn, types[] }` | `[{ type: PERSONAL_DATA \| BKI_COMPANY \| BKI_PERSONAL, given_by, given_at }]` |
| `GET /contracts`, `GET /contracts/{id}` | `Contract` |
| `GET /contracts/{id}/schedule?version=` | `[{ n, due_date, payment, principal, interest, balance }]` |
| `GET /contracts/{id}/operations` | `[{ contract_date, type, amount, actor, balance_after }]`, новые сверху |
| `GET /contracts/{id}/versions` | `[{ version_no, changes: [{ field, old, new }], author, reason, created_at }]`, новые сверху |
| `POST /contracts/{id}/tranches` `{ amount }`, `…/tranches/{tid}/repay` `{ amount }` | 422 — текст под полем |
| `POST /contracts/{id}/repayments` `{ kind: PARTIAL \| FULL, amount }` | `Contract` |
| `POST /contracts/{id}/emulation/next-month`, `/rewind` `{ target_date }` | `{ contract_date, operations[] }` |
| `POST /contracts/{id}/emulation/reset` | `Contract` |
| `GET /analyst/applications?queue=manual&inn=&status=&risk_class=&page=&size=` | `{ items: [{ id, company_name, inn, product_code, variant, amount, status, risk_class, manual_reasons[], submitted_at }], total }` |
| `GET /analyst/applications/{id}/report` | `{ application, provided, sources[{ name, status: OK \| NO_RESPONSE, attempts }], comparisons[{ check, what, values, passed }], scoring{ total, max, risk_class, debt_load_percent } \| null, decision{ summary, recommended_action }, history, contract{ id, number } \| null, can_approve }` |
| `POST /analyst/applications/{id}/approve \| counter-offer \| request-info \| reject \| recheck` | тела — как в макетах; 409 — одобрить нельзя, пока не ответили источники |
| `PATCH /analyst/contracts/{id}` `{ rate?, term_months?, comment }` | `Contract` |

`Application`: `id, inn, company_name, company (ЕГРЮЛ, с этапа CHECKING), purpose, product_code, variant, amount, term_months, owner_guarantee_consent, has_account, collateral { type, description, estimated_value }, status, step_label, is_final, allowed_actions[], statements[], info_request { source: SYSTEM | ANALYST, title, items[{ code: STATEMENT | COLLATERAL, message }] }, decision { outcome, reasons[], offers[{ id, product_code, variant, amount, term_months, rate }], rate, expires_at }, contract_id, updated_at, submitted_at`.

`Contract`: `id, number, company_name, inn, product_code, variant, status: ACTIVE | CLOSED, rate, term_months, contract_date, limit, amount, available, outstanding, next_payment { date, amount }, tranche_min, tranches[{ id, amount, issued_at, due_at, outstanding }], current_version`.

Типы операций договора, которые фронтенд подписывает по-русски: `LOAN_ISSUED, LIMIT_OPENED, TRANCHE_ISSUED, TRANCHE_REPAID, INTEREST_ACCRUED, INTEREST_PAID, SCHEDULED_PAYMENT, PARTIAL_REPAYMENT, FULL_REPAYMENT` ([src/lib/labels.js](src/lib/labels.js)).

## Скриншоты

```bash
npm run dev
npm run shots -- login register
```

Каждый снимок делается в чистом профиле браузера, без входа, поэтому так снимаются только открытые страницы. Снимки 1440 × 900 и 360 × 800 появятся в `shots/`. Нужен Chrome или Edge, путь можно задать через `CHROME_PATH`.
