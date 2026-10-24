# БКИ — Бюро кредитных историй

## Владелец

Коммерческие организации под надзором Банка России. Крупнейшие: НБКИ, ОКБ, Скоринг Бюро.

## Доступ

- **Для банков:** закрытый API, спецификация v3.0 утверждена Указанием ЦБ № 7189-У от 29.09.2025.
- **Для физлиц:** 2 раза в год бесплатно через Госуслуги или личный кабинет БКИ.
- **Публичного API нет.** Третьи лица без лицензии не могут запрашивать кредитные истории.

## Что берём для конвейера

По скоупу нужны данные для трёх целей:
1. **Стоп-фактор 3** — текущая просрочка > 30 дней у компании, ИП (как физлица), директора или учредителя с долей ≥ 20%.
2. **Скоринг** — долговая нагрузка, кредитная история, закрытые кредиты без просрочек, обращения за кредитами за 3 месяца.
3. **Предварительные предложения** — те же данные, но в режиме предоценки.

### Структура кредитного отчёта (упрощённая для мока)

Реальный отчёт БКИ содержит сотни полей. Для мока нужны только те, которые использует скоринг и стоп-факторы.

#### Кредитные обязательства

| Поле | Тип | Описание |
|---|---|---|
| contract_id | string | Номер договора |
| creditor_name | string | Название кредитора (банк / МФО) |
| credit_type | string | Тип: credit, credit_line, mortgage, microfinance |
| amount | number | Сумма кредита |
| balance | number | Текущий остаток долга |
| monthly_payment | number | Ежемесячный платёж |
| opened_date | date | Дата открытия |
| closed_date | date \| null | Дата закрытия (null = действующий) |
| status | string | open, closed |
| overdue_days | number | Текущая просрочка в днях (0 = нет) |
| overdue_amount | number | Сумма текущей просрочки |
| max_overdue_days_12m | number | Максимальная просрочка за последние 12 месяцев |
| had_overdue_30plus_12m | boolean | Были ли просрочки > 30 дней за последние 12 месяцев |

#### Обращения за кредитами (inquiries)

| Поле | Тип | Описание |
|---|---|---|
| date | date | Дата обращения |
| creditor_name | string | Куда обращались |
| amount | number | Запрошенная сумма |
| purpose | string | Цель (credit, credit_card, mortgage) |

#### Агрегаты (считаем на бэкенде, но удобно иметь в моке)

| Поле | Тип | Описание |
|---|---|---|
| total_monthly_payments | number | Сумма ежемесячных платежей по всем открытым кредитам |
| has_current_overdue_30plus | boolean | Есть ли текущая просрочка > 30 дней → стоп-фактор |
| has_closed_without_overdue | boolean | Есть ли закрытые кредиты без просрочек → скоринг +10 |
| worst_status_12m | string | clean / overdue_under_30 / overdue_over_30 / current_overdue_under_30 / current_overdue_over_30 |
| inquiries_last_3m | number | Число обращений за последние 3 месяца → скоринг (> 3 = −10) |
| has_history | boolean | Есть ли кредитная история вообще → скоринг (нет = 0) |

## Контракт мока

```
GET /api/mock/bki/{inn_or_personal_inn}

Query params:
  subject_type=company|person    // компания или физлицо (директор/учредитель/ИП)

Response 200:
{
  "subject_inn": "7726402390",
  "subject_type": "company",
  "report_date": "2026-10-24",

  "credits": [
    {
      "contract_id": "КД-2024-001234",
      "creditor_name": "ПАО Сбербанк",
      "credit_type": "credit",
      "amount": 5000000,
      "balance": 3200000,
      "monthly_payment": 120000,
      "opened_date": "2024-03-15",
      "closed_date": null,
      "status": "open",
      "overdue_days": 0,
      "overdue_amount": 0,
      "max_overdue_days_12m": 0,
      "had_overdue_30plus_12m": false
    },
    {
      "contract_id": "КД-2022-005678",
      "creditor_name": "АО Альфа-Банк",
      "credit_type": "credit",
      "amount": 2000000,
      "balance": 0,
      "monthly_payment": 0,
      "opened_date": "2022-06-01",
      "closed_date": "2024-06-01",
      "status": "closed",
      "overdue_days": 0,
      "overdue_amount": 0,
      "max_overdue_days_12m": 0,
      "had_overdue_30plus_12m": false
    }
  ],

  "inquiries": [
    {
      "date": "2026-09-10",
      "creditor_name": "ПАО ВТБ",
      "amount": 3000000,
      "purpose": "credit"
    }
  ],

  "summary": {
    "total_monthly_payments": 120000,
    "has_current_overdue_30plus": false,
    "has_closed_without_overdue": true,
    "worst_status_12m": "clean",
    "inquiries_last_3m": 1,
    "has_history": true
  }
}

Response 404:
{"error": "not_found", "message": "Кредитная история не найдена"}

Response 503:
{"error": "service_unavailable", "message": "БКИ не отвечает"}
```

## Примечания

- Запрос по компании и по физлицу — один эндпоинт, разный `subject_type`.
- Для ООО на стоп-фактор 3 нужно запросить: компанию (по ИНН компании), директора (по ИНН физлица), каждого учредителя с долей ≥ 20% (по ИНН физлица).
- Для ИП — только самого ИП (по ИНН физлица, `subject_type=person`).
- Согласие на запрос БКИ обязательно; в моке считаем его всегда полученным.
