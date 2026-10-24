# Обороты по расчётному счёту (внутренняя система банка)

## Владелец

Внутренняя АБС (автоматизированная банковская система). Доступ только у самого банка.

## Доступ

- Внутренний API банка. Публичного доступа нет.
- Альтернатива для клиентов без счёта — выписка 1С (см. 04-1c-statement.md).

## Что берём для конвейера

| Поле | Тип | Описание | Зачем нужно |
|---|---|---|---|
| month | date | Первый день месяца | группировка |
| incoming | number | Поступления за месяц (₽) | выручка |
| outgoing | number | Списания за месяц (₽) | информационно, отчёт |
| top_payers | array | Крупнейшие плательщики | отчёт по заявке (раздел 7) |

### Агрегаты (считаем на бэкенде)

| Агрегат | Формула | Зачем |
|---|---|---|
| annual_revenue | Σ incoming за 12 мес | лимит от выручки, скоринг |
| avg_monthly_revenue | annual_revenue / 12 | долговая нагрузка |
| revenue_trend | (incoming Q3+Q4) / (incoming Q1+Q2) | скоринг: динамика выручки |
| has_12m_history | count(months) ≥ 12 | можно ли считать динамику |

## Контракт мока

```
GET /api/mock/account-turnover/{inn}

Response 200:
{
  "inn": "7726402390",
  "has_account": true,
  "months": [
    {
      "month": "2025-11-01",
      "incoming": 2100000,
      "outgoing": 1850000,
      "top_payers": [
        {"name": "ООО Ашан", "amount": 800000, "share_percent": 38.1},
        {"name": "ИП Петров", "amount": 450000, "share_percent": 21.4}
      ]
    },
    {
      "month": "2025-12-01",
      "incoming": 2300000,
      "outgoing": 1950000,
      "top_payers": [
        {"name": "ООО Ашан", "amount": 900000, "share_percent": 39.1},
        {"name": "ИП Петров", "amount": 500000, "share_percent": 21.7}
      ]
    }
    // ... 12 месяцев
  ],
  "summary": {
    "annual_revenue": 24000000,
    "avg_monthly_revenue": 2000000
  }
}

// Клиент без счёта в нашем банке
Response 200:
{
  "inn": "772426617833",
  "has_account": false,
  "months": [],
  "summary": null
}

Response 503:
{"error": "service_unavailable", "message": "АБС не отвечает"}
```
