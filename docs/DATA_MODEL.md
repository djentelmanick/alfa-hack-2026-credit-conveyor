# Модель данных

PostgreSQL 16, схема создаётся миграциями Alembic (`backend/alembic/versions`), ORM-модели лежат в `backend/app/infra/db/models.py`. Файлы (выписки 1С и PDF договоров) хранятся в S3 (MinIO), в БД остаются только ключи объектов. Доменная модель, из которой собираются эти таблицы, описана в `backend/docs/BACKEND_SPEC.md`, раздел 4.

```mermaid
erDiagram
    users ||--o{ user_companies : "привязан к компаниям"
    users ||--o{ consents : "даёт согласия"
    users ||--o{ applications : "подаёт"
    applications ||--o{ application_consents : "согласия на момент подачи"
    applications ||--o{ bank_statements : "выписки"
    bank_statements ||--o{ statement_operations : "операции"
    applications ||--o{ source_snapshots : "ответы источников"
    applications ||--o{ check_results : "проверки"
    applications ||--o{ scoring_factors : "факторы скоринга"
    applications ||--o{ decisions : "решения"
    decisions ||--o{ offer_options : "варианты предложения"
    applications ||--o{ info_requests : "дозапросы"
    applications ||--o{ status_transitions : "история статусов"
    applications ||--o| contracts : "договор после выдачи"
    contracts ||--o{ contract_versions : "версии и PDF"
    contract_versions ||--o{ schedule_items : "график платежей"
    contracts ||--o{ operations : "операции"
    contracts ||--o{ tranches : "транши ВКЛ"

    users {
        uuid id PK
        string login UK
        string password_hash "argon2"
        enum role "CLIENT, ANALYST"
        string full_name
        string inn UK "ИНН физлица"
        bool passport_verified
        timestamptz created_at
    }
    user_companies {
        uuid user_id PK,FK
        string inn PK
        string name
        enum kind "LLC, IE"
        bool is_director
        jsonb authority "роль, основание, доверенность"
    }
    consents {
        string inn PK
        uuid given_by PK,FK
        enum type PK "BKI_COMPANY, BKI_PERSONAL, PERSONAL_DATA"
        timestamptz given_at
    }
    applications {
        uuid id PK
        uuid applicant_id FK
        string inn
        enum company_kind
        string company_name
        string okved
        enum purpose
        enum product_code "LUMP_SUM, REVOLVING_LINE"
        enum variant "UNSECURED, SECURED"
        numeric amount
        int term_months
        bool owner_guarantee
        jsonb authority
        enum collateral_type
        numeric collateral_value
        enum status
        timestamptz expires_at "срок одобрения"
        uuid pipeline_run_id "идемпотентность конвейера"
        string signing_code
        int scoring_total
        string scoring_risk_class "A-D"
        float scoring_debt_load
    }
    application_consents {
        uuid application_id PK,FK
        int position PK
        enum type
        uuid given_by
        timestamptz given_at
    }
    bank_statements {
        uuid id PK
        uuid application_id FK
        string file_key "ключ в S3"
        string bank_name
        string account_inn
        date period_from
        date period_to
        enum signature_status "VALID, INVALID, ABSENT"
    }
    statement_operations {
        uuid statement_id PK,FK
        int position PK
        date date
        numeric amount
        enum direction
        string counterparty_inn
    }
    source_snapshots {
        uuid application_id PK,FK
        int position PK
        enum source "EGRUL, BKI, TURNOVER, FNS, ..."
        enum status "OK, FAILED, SKIPPED"
        int attempts
        jsonb payload "ответ источника"
        timestamptz fetched_at
    }
    check_results {
        uuid application_id PK,FK
        int position PK
        string code
        enum stage "FORM, CHECKING"
        text expected
        text actual
        enum outcome
    }
    scoring_factors {
        uuid application_id PK,FK
        int position PK
        string code
        string label
        string value
        int points
    }
    decisions {
        uuid id PK
        uuid application_id FK
        string rule_code
        enum outcome
        numeric rate
        string_array reasons
        uuid decided_by "аналитик или пусто"
        text comment
    }
    offer_options {
        uuid id PK
        uuid decision_id FK
        enum product_code
        enum variant
        numeric amount
        int term_months
        numeric rate
    }
    info_requests {
        uuid application_id PK,FK
        int position PK
        string_array items
        jsonb item_messages
        text message
        timestamptz resolved_at
    }
    status_transitions {
        uuid application_id PK,FK
        int position PK
        enum from_status
        enum to_status
        uuid actor_id
        text reason
        timestamptz at
    }
    contracts {
        uuid id PK
        uuid application_id FK,UK
        string number UK
        enum product_code
        enum variant
        numeric rate
        int term_months
        date contract_date "дата эмуляции"
        enum status "ACTIVE, CLOSED"
        numeric principal
        numeric outstanding
        numeric limit_amount
        numeric available
    }
    contract_versions {
        uuid contract_id PK,FK
        int version_no PK
        string document_key "PDF в S3"
        jsonb changes
        text reason
    }
    schedule_items {
        uuid contract_id PK,FK
        int version_no PK,FK
        int position PK
        date due_date
        numeric principal
        numeric interest
        numeric balance
    }
    operations {
        uuid id PK
        uuid contract_id FK
        enum type
        numeric amount
        date contract_date
        numeric balance_after
        uuid tranche_id
        bool emulated
    }
    tranches {
        uuid id PK
        uuid contract_id FK
        numeric amount
        date issued_at
        date due_at
        numeric outstanding
    }
```

Отдельно от схемы заявок:

| Таблица | Назначение |
|---|---|
| `audit_log` | кто, что и над каким объектом сделал (`actor_id`, `action`, `target_id`, `details`), в том числе каждое действие аналитика с комментарием |
| `mock_failures` | включённые поломки моков по паре «источник, ИНН» для демо (`/dev/mocks/failures`), общие для `api` и `worker` |

Соглашения:

- деньги хранятся в `numeric(18,2)`, ставки в `numeric(5,2)`, в API они уходят строкой (`"3600000.00"`);
- перечисления (`enum` на схеме) хранятся строками `varchar(32)`, набор значений задаёт код, а не тип в БД;
- время везде `timestamptz` в UTC;
- дочерние таблицы заявки и договора упорядочены полем `position` и удаляются каскадом вместе с родителем;
- ответы источников хранятся как есть в `source_snapshots.payload`: по ним аналитик видит, что именно вернул каждый реестр, а повторный запрос в источник не нужен.
