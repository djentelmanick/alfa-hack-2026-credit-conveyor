# «Прозрачный бизнес» (ФНС)

## Владелец

ФНС России.

## Доступ

- **Веб-сервис:** https://pb.nalog.ru — бесплатно, без регистрации. API нет, только веб с капчей.
- **Коммерческие API:** checko.ru, ofdata.ru — агрегируют эти же данные.

## Что берём для конвейера

Из «Прозрачного бизнеса» берём то, чего нет в ЕГРЮЛ:

| Поле | Тип | Описание | Зачем |
|---|---|---|---|
| mass_address | boolean | Адрес массовой регистрации (≥ 10 ЮЛ по адресу) | скоринг: −10 |
| mass_director | boolean | Руководитель является массовым (руководит ≥ 5 ЮЛ) | скоринг: −10 |
| director_company_count | number | Сколькими ЮЛ руководит | для оценки |
| mass_founder | boolean | Учредитель является массовым (участвует в ≥ 10 ЮЛ) | скоринг: −10 |
| founders_company_counts | array | Сколькими ЮЛ владеет каждый учредитель | для оценки |
| director_disqualified | boolean | Руководитель дисквалифицирован | стоп-фактор 8 |
| disqualification_period | object\|null | Период дисквалификации (from, to) | информационно |
| tax_debt_enforcement | number | Задолженность > 1000 ₽, направленная на взыскание приставу (₽) | повод для ручной проверки 7 (> 10 тыс ₽) |
| tax_debt_enforcement_date | date | Дата данных | информационно |
| arrears_and_penalties | number | Недоимка и задолженность по пеням и штрафам (₽) | повод для ручной проверки 7 |
| arrears_and_penalties_date | date | Дата данных | информационно |
| tax_reporting_missing | boolean | Не представляет налоговую отчётность > 1 года | информационно |
| employee_count | number | Среднесписочная численность работников | информационно |
| employee_count_year | number | За какой год | информационно |
| taxes_paid | number | Уплаченные налоги и сборы за год (₽) | информационно |
| revenue_accounting | number | Доход по бухотчётности (₽) | информационно (дублирует ГИР БО) |
| expense_accounting | number | Расход по бухотчётности (₽) | информационно |

## Контракт мока

```
GET /api/mock/fns-transparent/{inn}

Response 200 (реальные данные ООО «М-ФУД»):
{
  "inn": "7726402390",
  "mass_address": false,
  "address_company_count": null,

  "director": {
    "full_name": "СОТИР ДМИТРИЙ ГЕОРГИЕВИЧ",
    "inn": "772600663920",
    "company_count": 8,                   // руководит 8 ЮЛ → массовый (≥ 5)
    "is_mass": true
  },
  "director_disqualified": false,
  "disqualification_period": null,

  "founders": [
    {"full_name": "МАГНИЦКАЯ ЕКАТЕРИНА НИКОЛАЕВНА", "inn": "773165163038", "company_count": 1, "is_mass": false},
    {"full_name": "СОТИР ДМИТРИЙ ГЕОРГИЕВИЧ", "inn": "772600663920", "company_count": 11, "is_mass": true},
    {"full_name": "МАТВИЕЦ ГЕННАДИЙ АНАТОЛЬЕВИЧ", "inn": "690100941220", "company_count": 9, "is_mass": false}
  ],
  "has_mass_founder": true,               // Сотир участвует в 11 ЮЛ (≥ 10)

  "tax_debt_enforcement": 0,              // задолженность, направленная на взыскание
  "tax_debt_enforcement_date": "2026-10-01",
  "arrears_and_penalties": 0,             // недоимка и задолженность по пеням/штрафам
  "arrears_and_penalties_date": "2026-09-01",
  "tax_reporting_missing": false,

  "employee_count": 31,
  "employee_count_year": 2025,
  "taxes_paid": 27460390,                // уплаченные налоги за 2025
  "revenue_accounting": 376942000,       // доход по бухотчётности за 2025
  "expense_accounting": 330914000        // расход по бухотчётности за 2025
}

Response 404:
{"error": "not_found", "message": "ИНН не найден"}

Response 503:
{"error": "service_unavailable", "message": "Сервис ФНС не отвечает"}
```

## Пример реального документа

См. [samples/pb-nalog-ooo-m-fud-7726402390.pdf](samples/pb-nalog-ooo-m-fud-7726402390.pdf) — выписка «Прозрачного бизнеса» для ООО «М-ФУД».

## Примечания

- Реестровые данные (статус, директор, учредители, ОКВЭД, адрес) берём из ЕГРЮЛ, не из «Прозрачного бизнеса» — они там дублируются.
- Сверять ЕГРЮЛ и «Прозрачный бизнес» не нужно: оба источника ведёт ФНС, расхождение — задержка обновления, а не риск клиента.
- Данные о налоговой задолженности обновляются раз в месяц и могут быть неактуальны — аналитик учитывает дату данных.
