"""Подписи для интерфейса. Домен оперирует кодами, а клиенту нужен понятный текст."""

from collections.abc import Mapping

from app.domain.labels import APPLICATION_STATUS_LABELS as STATUS_LABELS

__all__ = ["CHECK_TITLES", "INFO_ITEM_LABELS", "REASON_LABELS", "STATUS_LABELS"]

REASON_LABELS: Mapping[str, str] = {
    # Стоп-факторы
    "COMPANY_INACTIVE": "Компания не действует: ликвидация, банкротство или реорганизация",
    "BUSINESS_AGE_UNDER_6M": "Бизнесу меньше 6 месяцев",
    "CURRENT_OVERDUE_OVER_30": "Текущая просрочка по кредитам больше 30 дней",
    "FORBIDDEN_OKVED": "Отрасль бизнеса не кредитуется",
    "TAX_BLOCK_OR_BAILIFF": "Счета заблокированы налоговой или есть исполнительные производства",
    "CBR_RED_LEVEL": "Высокий уровень риска на платформе ЦБ «Знай своего клиента»",
    "ROSFIN_LIST": "Компания или её участники в перечне Росфинмониторинга",
    "DIRECTOR_DISQUALIFIED": "Руководитель дисквалифицирован",
    # Поводы ручной проверки
    "SECURED_VARIANT": "Нужно проверить залог",
    "STATEMENT_UNVERIFIED": "Выписку из другого банка не удалось подтвердить",
    "AMOUNT_OVER_5M": "Сумма больше 5 млн ₽",
    "RECENT_OWNER_CHANGE": "Недавно сменился руководитель или собственник",
    "UNRELIABLE_REGISTRY_RECORD": "В ЕГРЮЛ есть запись о недостоверности сведений",
    "CBR_YELLOW_LEVEL": "Средний уровень риска на платформе ЦБ «Знай своего клиента»",
    "TAX_DEBT_OVER_10K": "Задолженность по налогам больше 10 000 ₽",
    "DIRECTOR_BAILIFF_OVER_10K": "Исполнительные производства руководителя больше 10 000 ₽",
    "NO_COUNTER_OFFER_AVAILABLE": "Подобрать другие условия автоматически не удалось",
    "SOURCE_UNAVAILABLE": "Не все источники ответили, заявку проверит специалист банка",
    "PIPELINE_FAILED": "Автоматическая проверка не завершилась, заявку проверит специалист банка",
    # Правила решения без отдельных причин
    "REVENUE_LIMIT_COUNTER_OFFER": (
        "Запрошенная сумма выше лимита от выручки для варианта без залога"
    ),
    "CLASS_A_APPROVE": "Заявка одобрена",
    "CLASS_B_APPROVE": "Заявка одобрена",
    "CLASS_C_COUNTER_OFFER": "Банк предлагает другие условия",
    "RISK_CLASS_D": "Высокий кредитный риск",
    "ANALYST_DECISION": "Решение принял специалист банка",
}

INFO_ITEM_LABELS: Mapping[str, str] = {
    "STATEMENT": "Выписка за последние 6–12 месяцев, последняя операция не старше месяца",
    "COLLATERAL": "Сведения о залоге",
}


# Заголовки строк «как сравнили» в отчёте аналитика. Стоп-факторы подписаны в REASON_LABELS.
CHECK_TITLES: Mapping[str, str] = {
    "FORM_COMPLETE": "Анкета заполнена полностью",
    "PURPOSE_ALLOWED": "Продукт подходит под цель",
    "TERMS_IN_RANGE": "Сумма и срок в границах продукта",
    "APPLICANT_AUTHORITY": "Полномочия заявителя: руководитель по ЕГРЮЛ или доверенность",
    "NO_ACTIVE_APPLICATION": "Нет другой активной заявки по ИНН",
    "COLLATERAL_COVERAGE": "Оценка залога и порог покрытия",
    "OWNER_GUARANTEE": "Согласие собственника на поручительство",
    "CONSENTS_GIVEN": "Согласия на БКИ и обработку данных",
    "STATEMENT_PERIOD": "Период и свежесть выписки",
    "BANK_SIGNATURE": "Подпись банка в выписке",
    "FNS_REVENUE_MATCH": "Выручка по выписке и по отчётности в ФНС",
}

SOURCE_CHECK_PREFIX = "SOURCE_"


def reason_label(code: str) -> str:
    return REASON_LABELS.get(code, code)


def check_title(code: str) -> str:
    if code in CHECK_TITLES:
        return CHECK_TITLES[code]
    if code.startswith(SOURCE_CHECK_PREFIX):
        return f"Ответ источника {code.removeprefix(SOURCE_CHECK_PREFIX)}"
    return reason_label(code)
