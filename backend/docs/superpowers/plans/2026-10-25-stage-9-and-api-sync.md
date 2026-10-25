# Этап 9 и сверка контракта API: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть расхождения OpenAPI с контрактом (пункты 7–14 сверки), реализовать операции по договору и эмуляцию (этап 9),
затем выполнить остаток этапа 10: чистый прогон e2e и README.

**Architecture:** операции живут в агрегате `Contract` (`LoanContract`, `CreditLineContract`), сервисы `ContractService`
и `EmulationService` грузят договор через `UnitOfWork`, вызывают метод агрегата и коммитят. PDF новой версии после
частичного досрочного погашения кладётся в S3 через существующий `ContractDocuments.commit_with_document`.
Эмуляция двигает только `contract.contract_date`, порт `Clock` не трогает.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, pytest.

**Spec:** `docs/BACKEND_SPEC.md` раздел 15, `../docs/SCOPE.md` разделы 2, 8 («После выдачи»), 12; `docs/API.md` разделы 2.7, 2.8, 4.6;
`docs/PHASES.md` этапы 9 и 10.

## Global Constraints

- Слои `api -> application -> domain <- infra`; домен без FastAPI/SQLAlchemy.
- Деньги `Decimal`/`Money`, в JSON строка с двумя знаками; ставка строкой.
- Все параметры в JSON-теле POST, ошибки только `ErrorOut`.
- Транш от 50 000 (`tranche_min` из `products.yaml`), срок транша 6 мес (`tranche_term_months`), не хардкодить.
- У ВКЛ с залогом выборка закрыта за 6 мес до конца линии (`draw_stop_months_before_end`, уже есть `RevolvingCreditLine.draw_until`).
- Эмуляция только при `DEMO_MODE=true`, иначе `404 NOT_FOUND` (как `/dev/mocks/failures`).
- Денег у клиента всегда хватает, просрочки не эмулируются.
- Готово, когда зелёные `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy app`.

## Решения, которые надо подтвердить (спека их не фиксирует)

1. **`ContractClock` отдельным классом не делаем:** «часы договора» это поле `Contract.contract_date`, которое двигают
   только методы агрегата. В BACKEND_SPEC раздел 15 допишу одну строку об этом.
2. **Версия после частичного досрочного погашения:** `reason` «Частичное досрочное погашение», автор клиент,
   `changes = [{field: "outstanding", old, new}]`. В API.md 4.6 комментарий о `field` расширю: `rate`, `term_months` или `outstanding`.
3. **Перемотка:** `target_date` не раньше `contract_date` и не позже `end_date` договора. Иначе 422 `VALIDATION_ERROR`:
   `details[].field = "target_date"`, коды `DATE_IN_PAST` и `DATE_AFTER_END`. Это защищает от бесконечного цикла у ВКЛ без залога, которая продлевается автоматически.
4. **`/contracts/repay` с `kind: "FULL"`:** `amount` не нужен, погашается весь долг. Если `amount` передан, он игнорируется.
   Для `PARTIAL` поле обязательно и должно быть меньше остатка долга, иначе 422 (`REQUIRED` или `AMOUNT_NOT_LESS_THAN_DEBT`: для полного погашения есть `FULL`).
5. **ВКЛ сама не закрывается** по окончании срока, закрывается только через `FULL`.
6. **`reset`** удаляет из S3 PDF версий 2 и далее после commit, без гарантии. Если S3 недоступен, остаётся сирота, пишем warning, как в `ContractDocuments._discard`.
7. **Тесты:** пишу те, что этап 9 перечисляет в `docs/PHASES.md` (сценарии 1 и 5 после выдачи, reset, независимость
   времени договора), плюс unit-тесты на границы операций (50 000, доступный остаток, окно выборки).
   Если unit-тесты на границы не нужны, скажи, уберу.

## Review Focus

- Последний плановый платёж: копейки, накопленные при округлении, не должны оставить договор открытым с долгом 0.01. Тест: перемотка до конца даёт `outstanding == "0.00"` и `CLOSED`.
- Частичное досрочное погашение после правки ставки аналитиком: график строится от текущей ставки, а дата последнего платежа не меняется.
- Договор подписан 31-го числа: месяцы сдвигаются через `add_months` от `start_date`, а не от прошлой даты договора, чтобы 31.01 → 28.02 → 31.03 не превратилось в 28.03. Тест в unit для `advance_month`.
- Повторный `reset` и операции после него: номера версий снова идут с 2, ключ PDF `v2` перезаписывается без ошибки.
- Клиент пытается провести операцию по чужому договору: 403, аналитик на клиентских операциях: 403.

---

### Task 1: Схема OpenAPI и документ (пункты 7–14 сверки)

**Files:**
- Modify: `app/api/v1/schemas/common.py` (`AUTH_ERRORS`, `ROLE_ERRORS` получают `422: {"model": ErrorOut}`)
- Modify: `app/domain/entities/parts.py:173` (`OfferOption.rate: Decimal`), `app/api/v1/schemas/application.py:249` (`rate: RateStr`)
- Modify: `app/api/v1/schemas/auth.py` (`LoginOut`), `app/api/v1/schemas/contract.py` (`SigningConfirmOut`), модели `SubmitOut`, `RecheckOut`
- Modify: `app/api/v1/applications.py` (`_ERRORS` по эндпоинтам)
- Modify: `docs/API.md` разделы 1, 4.2, 4.4
- Test: `tests/integration/api/test_openapi_contract.py` (новый)

- [ ] **Step 1: Failing test** `test_every_422_is_error_out`: в `create_app().openapi()` у каждой операции ответ `422` ссылается на `#/components/schemas/ErrorOut`, а `HTTPValidationError` в `components.schemas` нет.
  `test_required_fields`: `OfferOptionOut.required` содержит `rate`, `LoginOut.required` содержит `token_type`, `SigningConfirmOut.required` содержит `status`; `SubmitOut.properties.status.const == "SUBMITTED"`, `RecheckOut.properties.status.const == "CHECKING"`.
- [ ] **Step 2:** `pytest -q tests/integration/api/test_openapi_contract.py`: FAIL.
- [ ] **Step 3:** 422 добавить в общие словари ошибок. `HTTPValidationError` пропадёт, когда ни одна операция на него не ссылается; если FastAPI всё равно добавит схему, удалить её в `create_app` через переопределение `app.openapi`.
  Поля с литералом сделать обязательными: `token_type: Literal["bearer"]`, `status: Literal["ISSUED"]` без значения по умолчанию, вызывающие места передают значение явно.
  `SubmitOut.status: Literal["SUBMITTED"]`, `RecheckOut.status: Literal["CHECKING"]`.
  `OfferOption.rate: Decimal` в домене. Места, где `rate=None` было допустимо, проверить через mypy.
- [ ] **Step 4:** пункт 14: прочитать сервисные методы `get`, `history`, `decision`, `validate` и убрать из `responses` коды, которых метод не бросает (ожидаемо 409 у `get`/`history`). Оставить объявленными только реально возможные.
- [ ] **Step 5:** `docs/API.md`: `StatementOut.last_operation_at?: ISODate` с пояснением «нет, если в выписке нет операций»; в разделе 1 строка «деньги и ставки во входных телах принимаются строкой или числом, в ответах всегда строка»; в `ErrorOut` поле `field: string` (всегда есть, для ошибки без поля `"body"`).
- [ ] **Step 6:** тест PASS, затем `pytest -q && ruff check . && ruff format --check . && mypy app`.

### Task 2: Операция знает транш и эмулятор (данные)

**Files:**
- Modify: `app/domain/entities/contract.py` (`Operation`, `Contract.record`)
- Modify: `app/infra/db/models.py` (`OperationRow`), `app/infra/db/mappers.py`
- Create: `alembic/versions/0006_operation_tranche_and_emulator.py`
- Modify: `app/api/v1/schemas/contract.py` (`OperationOut.from_domain`)
- Test: `tests/integration/db/` (существующий тест маппинга договора дополнить кругом с `tranche_id` и `emulated`)

**Interfaces:**
- Produces: `Operation.tranche_id: UUID | None = None`, `Operation.emulated: bool = False`;
  `Contract.record(type, amount, *, actor_id: UUID | None, tranche_id: UUID | None = None, emulated: bool = False) -> Operation`.
  `OperationOut.actor`: `"EMULATOR"` если `emulated`, иначе `"SYSTEM"` при `actor_id is None`, иначе `str(actor_id)`.

- [ ] **Step 1:** failing test: договор ВКЛ с операцией `TRANCHE_DRAWN` (`tranche_id` задан) и `INTEREST_ACCRUED` (`emulated=True`) после `commit` и повторного чтения возвращает те же поля.
- [ ] **Step 2:** миграция 0006: `operations.tranche_id UUID NULL`, `operations.emulated BOOLEAN NOT NULL DEFAULT false`; downgrade удаляет колонки.
- [ ] **Step 3:** модель, маппер, `OperationOut`. Тест PASS, полный прогон.

### Task 3: Операции в агрегате договора (домен)

**Files:**
- Modify: `app/domain/entities/contract.py`
- Modify: `app/domain/products/loan_product.py` (`RevolvingCreditLine.draw_errors`)
- Test: `tests/unit/domain/test_contract_operations.py` (новый)

**Interfaces (Produces):**
- `Contract.advance_month() -> list[Operation]`: абстрактный. Новая дата `add_months(start_date, full_months_between(start_date, contract_date) + 1)`, события с датой в `(старая, новая]`. Закрытый договор: `InvalidStatus`.
- `Contract.move_date(day: date) -> list[Operation]`: то же для произвольной даты, используется перемоткой. Месячные события берутся по годовщинам `start_date` в интервале.
- `Contract.repay_full(actor_id: UUID) -> Operation`: `FULL_REPAYMENT` на сумму долга, долг 0, `status = CLOSED`.
- `Contract.reset_to_issued() -> list[str]`: оставляет `versions[:1]` и `operations[:1]`, `contract_date = start_date`, `ACTIVE`,
  ставку и срок восстанавливает, проходя `changes` версий от последней к первой (`old` для `rate`/`term_months`).
  Возвращает `document_key` удалённых версий.
- `LoanContract.prepay(amount: Money, *, schedule, document_key, author_id, now) -> ContractVersion`: `PARTIAL_PREPAYMENT`, новая версия с `changes=[("outstanding", old, new)]`, `reason = "Частичное досрочное погашение"`.
- `LoanContract.advance_month`: для пунктов графика текущей версии с датой в интервале `SCHEDULED_PAYMENT` (`amount = payment`, `emulated=True`), `outstanding -= principal`. На последнем пункте `principal = outstanding`. При долге 0 договор `CLOSED`.
- `CreditLineContract.draw(amount: Money, *, due_at: date, actor_id: UUID) -> Tranche`, `repay_tranche(tranche_id: UUID, amount: Money, *, actor_id: UUID | None, emulated: bool = False) -> Operation`.
  `advance_month`: на каждую годовщину в интервале `INTEREST_ACCRUED` по каждому траншу с долгом (`outstanding * rate / 1200`, долг не меняется, `tranche_id` задан);
  затем транши с `due_at <= новая дата` гасятся эмулятором (`TRANCHE_REPAID`), `available` восстанавливается.
- `RevolvingCreditLine.draw_errors(contract, amount, today) -> list[FieldError]`: коды `TRANCHE_BELOW_MIN`, `OVER_AVAILABLE`, `DRAW_WINDOW_CLOSED` (field `amount`). `can_draw` переписать через него.
- Ошибки: транш больше долга `ValidationFailed("amount", "OVER_TRANCHE_DEBT")`, неизвестный транш `NotFound`.

- [ ] **Step 1: failing tests** (значения из SCOPE: 2 000 000, 12 мес, 23,5%; лимит 12 000 000, 60 мес, 23%):
  - `test_three_months_give_three_scheduled_payments`: три `advance_month` дают ровно три `SCHEDULED_PAYMENT`, `outstanding` равен `balance` третьего пункта графика.
  - `test_partial_prepayment_lowers_payment_keeps_term`: после 3 месяцев `prepay(500 000)` создаёт версию 2, в её графике 9 пунктов, последняя дата `add_months(start, 12)`, платёж меньше прежнего.
  - `test_last_payment_closes_contract`: 12 месяцев дают `outstanding == Money.zero()` и `CLOSED`, следующий `advance_month` даёт `InvalidStatus`.
  - `test_month_shift_from_31st`: `start_date = 2026-01-31`, два сдвига дают `2026-02-28`, затем `2026-03-31`.
  - `test_tranche_bounds`: `draw_errors` на `49 999.99` даёт `TRANCHE_BELOW_MIN`, на `50 000` пусто; сумма больше `available` даёт `OVER_AVAILABLE`; дата `draw_until` даёт `DRAW_WINDOW_CLOSED`.
  - `test_repaying_tranche_restores_available`: транши 1 000 000 и 2 000 000, `available == 9 000 000`; гашение первого целиком даёт `10 000 000`.
  - `test_tranche_auto_repaid_at_due`: 6 `advance_month` после выдачи транша дают 6 `INTEREST_ACCRUED` и один `TRANCHE_REPAID` (`emulated`).
  - `test_reset_restores_issued_state`: после операций, правки ставки и prepay `reset_to_issued` оставляет одну версию, одну операцию, исходные ставку, срок и долг, возвращает ключи удалённых версий.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS, `mypy app`.

### Task 4: Клиентские операции (`ContractService` и `/contracts/*`)

**Files:**
- Modify: `app/application/contract_service.py`, `app/api/v1/contracts.py`, `app/api/v1/schemas/contract.py`
- Modify: `app/domain/enums.py` (`RepaymentKind = PARTIAL | FULL`), `app/domain/entities/audit.py` (`AuditAction` для операций, если аудит ведётся у правки договора; ведём одинаково)

**Interfaces:**
- Consumes: Task 3.
- Produces: `ContractService.draw_tranche(claims, contract_id, amount) -> Tranche`,
  `repay_tranche(claims, contract_id, tranche_id, amount) -> ContractView`,
  `repay(claims, contract_id, kind: RepaymentKind, amount: Money | None) -> ContractView`.
  Только `Role.CLIENT` и только свой договор (`_visible`), аналитику `Forbidden`.
  Договор не ВКЛ для траншей и `PARTIAL` для ВКЛ: `InvalidStatus` (409). Частичное погашение через `ContractDocuments.commit_with_document`, график через `product.reschedule(contract, contract.rate, contract.term_months)`.
- DTO: `TrancheDrawIn {contract_id, amount}`, `TrancheRepayIn {contract_id, tranche_id, amount}`, `RepayIn {contract_id, kind, amount?}`; `amount` с `gt=0`.
  Ответы: `201 TrancheOut`, `200 ContractOut`, `200 ContractOut`.

- [ ] **Step 1:** реализация сервиса и роутеров по образцу `analyst.edit_contract` (лог с `contract_id`).
- [ ] **Step 2:** проверка через e2e в Task 6. Отдельные тесты сервиса не пишем.

### Task 5: Эмуляция (`EmulationService` и `/contracts/emulation/*`)

**Files:**
- Create content: `app/application/emulation_service.py`, `app/api/v1/emulation.py` (сейчас пустые)
- Modify: `app/main.py` (сборка сервиса и `include_router`), `app/api/v1/deps.py`, `app/api/v1/schemas/contract.py` (`RewindIn`, `EmulationResultOut`)
- Modify: `docs/API.md` 2.8 и 4.6 (коды 422 перемотки, `field: "outstanding"`), `docs/BACKEND_SPEC.md` 15 (решение про `ContractClock`)

**Interfaces:**
- Produces: `EmulationResult(contract_date: date, operations: list[Operation], contract: ContractView)`;
  `EmulationService.next_month(claims, contract_id) -> EmulationResult`, `rewind_to(claims, contract_id, target: date) -> EmulationResult`,
  `reset(claims, contract_id) -> ContractView`. Видимость как в `ContractService._visible` (клиент свой, аналитик любой).
  `rewind_to`: `advance_month` в цикле, пока следующая годовщина `<= target` и договор активен, затем `move_date(target)`.
- Роутер: зависимость `_demo_container` из `dev.py` вынести в `deps.py` и использовать в обоих (вне демо 404).

- [ ] **Step 1:** реализация, сборка в `main.py`. **Step 2:** проверка через e2e в Task 6.

### Task 6: e2e после выдачи

**Files:**
- Modify: `tests/e2e/test_scenario_1_kolos.py`, `tests/e2e/test_scenario_5_techprom.py`
- Create: `tests/e2e/test_emulation.py`

- [ ] **Step 1:** `test_kolos_three_months_prepayment_and_full_rewind`: после подписания три `emulation/next-month` дают три `SCHEDULED_PAYMENT` в `/contracts/operations` с `actor == "EMULATOR"`; `/contracts/repay {kind: PARTIAL, amount: "500000.00"}` даёт `version_no == 2`, `next_payment.amount` меньше прежнего, последний пункт `/contracts/schedule` с той же датой; PDF версии 2 есть в S3 (`contract_files`); `rewind` до `end_date` даёт `status == "CLOSED"`, `outstanding == "0.00"`.
- [ ] **Step 2:** `test_techprom_two_tranches_and_limit_restored`: транши `"1000000.00"` и `"2000000.00"` дают `available == "9000000.00"`; `tranches/repay` первого целиком даёт `"10000000.00"`; транш `"49999.99"` даёт 422 с `details[0].code == "TRANCHE_BELOW_MIN"`.
- [ ] **Step 3:** `test_emulation.py`: `test_reset_returns_contract_to_issue` (после операций одна версия, одна операция, `contract_date` равна дате подписания, PDF версии 1 на месте; после reset частичное погашение снова даёт `version_no == 2`); `test_emulation_does_not_touch_offer_deadlines` (Соколов в `COUNTER_OFFER`, перемотка договора Колоса на 11 мес, у заявки Соколова тот же `expires_at` и статус); `test_emulation_hidden_without_demo_mode` (404); `test_client_cannot_operate_foreign_contract` (403); `test_rewind_rejects_past_and_after_end` (422 `DATE_IN_PAST`, `DATE_AFTER_END`).
- [ ] **Step 4:** `pytest -q && ruff check . && ruff format --check . && mypy app`.

### Task 7: Остаток этапа 10

- [ ] **Step 1:** `docker compose down -v && docker compose up --build -d`, `alembic upgrade head`, `seed`; `pytest -q tests/e2e` одним прогоном. Сохранить вывод.
- [ ] **Step 2:** перегенерировать `/openapi.json` с поднятого `api` и сверить ещё раз скриптом из сверки: эндпоинтов из пунктов 1–6 больше нет в списке расхождений.
- [ ] **Step 3:** логирование: в сервисах договора и эмуляции `application_id` или `contract_id` в каждом сообщении (`grep -n "logger\." app/application`).
- [ ] **Step 4:** секреты: `git grep -nIE "(secret|password|key)\s*=\s*['\"][^'\"]{8,}"` и проверка `.env`, `.gitignore`. В `.env.example` только локальные значения с пометкой.
- [ ] **Step 5:** `README.md`: запуск (compose, миграции, seed), тестовые учётки из `seed.py`, как пройти пять сценариев на демо (включая эмуляцию для 1 и 5), как сломать источник (`/dev/mocks/failures`), как запускать тесты.
