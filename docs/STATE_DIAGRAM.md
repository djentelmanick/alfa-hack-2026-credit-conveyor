# Диаграмма состояний заявки

Статус меняется только через `ApplicationStateMachine.transition(...)` (`backend/app/domain/state_machine.py`). Переход, которого нет в таблице ниже, запрещён: API отвечает `409 INVALID_STATUS`. Каждый переход пишется в `status_transitions` и уходит клиенту и аналитику по WebSocket `/api/v1/ws/applications` событием `application.status_changed`.

```mermaid
stateDiagram-v2
    [*] --> DRAFT: POST /applications/create
    DRAFT --> SUBMITTED: submit
    DRAFT --> CANCELLED: cancel
    SUBMITTED --> CHECKING: worker взял задачу
    CHECKING --> REJECTED: стоп-фактор
    CHECKING --> NEEDS_INFO: не хватает данных
    CHECKING --> MANUAL_REVIEW: источник не ответил
    CHECKING --> SCORING: проверки пройдены
    SCORING --> APPROVED: класс A/B, условия в лимитах
    SCORING --> COUNTER_OFFER: сумма выше лимита или класс C
    SCORING --> MANUAL_REVIEW: повод для ручной проверки
    SCORING --> REJECTED: класс D
    NEEDS_INFO --> SUBMITTED: update и submit
    NEEDS_INFO --> CANCELLED: cancel
    MANUAL_REVIEW --> APPROVED: analyst approve
    MANUAL_REVIEW --> COUNTER_OFFER: analyst counter-offer
    MANUAL_REVIEW --> NEEDS_INFO: analyst request-info
    MANUAL_REVIEW --> REJECTED: analyst reject
    MANUAL_REVIEW --> CHECKING: analyst recheck
    APPROVED --> SIGNING: accept
    APPROVED --> CANCELLED: cancel или 7 дней
    COUNTER_OFFER --> SIGNING: accept варианта без залога
    COUNTER_OFFER --> NEEDS_INFO: accept варианта с залогом
    COUNTER_OFFER --> CANCELLED: cancel или 7 дней
    SIGNING --> ISSUED: signing/confirm
    SIGNING --> CANCELLED: cancel
    ISSUED --> [*]
    REJECTED --> [*]
    CANCELLED --> [*]
```

| Из | В | Кто инициирует |
|---|---|---|
| DRAFT | SUBMITTED, CANCELLED | клиент |
| SUBMITTED | CHECKING | система (worker) |
| CHECKING | REJECTED, NEEDS_INFO, MANUAL_REVIEW, SCORING | система |
| NEEDS_INFO | SUBMITTED, CANCELLED | клиент |
| SCORING | APPROVED, COUNTER_OFFER, MANUAL_REVIEW, REJECTED | система |
| MANUAL_REVIEW | APPROVED, COUNTER_OFFER, NEEDS_INFO, REJECTED, CHECKING | аналитик |
| APPROVED | SIGNING | клиент |
| APPROVED | CANCELLED | клиент или `ExpirationJob` через 7 дней |
| COUNTER_OFFER | SIGNING, NEEDS_INFO | клиент |
| COUNTER_OFFER | CANCELLED | клиент или `ExpirationJob` через 7 дней |
| SIGNING | ISSUED, CANCELLED | клиент |

Активные статусы: все, кроме `ISSUED`, `REJECTED` и `CANCELLED`. По одному ИНН может быть только одна активная заявка (`409 ACTIVE_APPLICATION_EXISTS`).

Кнопки в интерфейсе берутся из `allowed_actions` в ответе API, поэтому фронтенд не дублирует таблицу переходов: `DRAFT` и `NEEDS_INFO` дают `EDIT`, `UPLOAD_STATEMENT`, `SUBMIT`, `CANCEL`; `APPROVED` и `COUNTER_OFFER` дают `ACCEPT`, `CANCEL`; `SIGNING` даёт `SIGN`, `CANCEL`.

## Договор после выдачи

```mermaid
stateDiagram-v2
    [*] --> ACTIVE: signing/confirm, версия 1 и PDF
    ACTIVE --> ACTIVE: платежи, частичное погашение, транши, правка аналитиком (новая версия)
    ACTIVE --> CLOSED: полное погашение или конец срока в эмуляции
    CLOSED --> ACTIVE: emulation/reset (только DEMO_MODE)
    CLOSED --> [*]
```

Подробности правил переходов и бизнес-причин: `docs/SCOPE.md` и `backend/docs/BACKEND_SPEC.md`, раздел 5.
