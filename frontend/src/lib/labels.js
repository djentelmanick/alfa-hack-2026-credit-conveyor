import { formatMoney } from "./format.js";

export const PRODUCT_NAMES = { LOAN: "Кредит одной суммой", CREDIT_LINE: "Кредитная линия" };
export const VARIANT_NAMES = { UNSECURED: "без залога", SECURED: "с залогом" };

export const productName = (code) => PRODUCT_NAMES[code] || code;
export const variantName = (code) => VARIANT_NAMES[code] || code;

/** «Кредит одной суммой · без залога · 2 000 000 ₽ · 12 мес» */
export function productLine(app, { term = true } = {}) {
  const parts = [productName(app.product_code), variantName(app.variant), formatMoney(app.amount)];
  if (term && app.term_months) parts.push(`${app.term_months} мес`);
  return parts.join(" · ");
}

/** Названия статусов в истории заявки. */
export const HISTORY_NAMES = {
  DRAFT: "Черновик",
  SUBMITTED: "Подана",
  CHECKING: "Проверка",
  NEEDS_INFO: "Нужны данные",
  SCORING: "Оценка",
  MANUAL_REVIEW: "У специалиста",
  APPROVED: "Одобрена",
  COUNTER_OFFER: "Решение",
  REJECTED: "Отказ",
  SIGNING: "Подписание",
  ISSUED: "Выдано",
  CANCELLED: "Отменена",
};

/** Фраза о заявке на главной: «Анна, ваша заявка на оценке». */
export const HOME_PHRASE = {
  DRAFT: "заявка ещё не отправлена",
  SUBMITTED: "ваша заявка отправлена",
  CHECKING: "ваша заявка на проверке",
  NEEDS_INFO: "банку не хватает данных по заявке",
  SCORING: "ваша заявка на оценке",
  MANUAL_REVIEW: "заявку смотрит специалист",
  APPROVED: "кредит одобрен",
  COUNTER_OFFER: "банк предлагает другие условия",
  SIGNING: "осталось подписать договор",
};

export const ACTIVE_STATUSES = ["DRAFT", "SUBMITTED", "CHECKING", "NEEDS_INFO", "SCORING", "MANUAL_REVIEW", "APPROVED", "COUNTER_OFFER", "SIGNING"];
export const isActive = (status) => ACTIVE_STATUSES.includes(status);

export const CONSENT_LABELS = {
  PERSONAL_DATA: "Согласие на обработку персональных данных",
  BKI_COMPANY: "Согласие на запрос кредитной истории компании",
  BKI_PERSONAL: "Согласие на запрос моей кредитной истории",
};

/** ИНН из 12 цифр — у ИП, из 10 — у компании. */
export const isSoleTrader = (inn) => String(inn || "").length === 12;

export const RISK_LABELS = { A: "A — высокий рейтинг", B: "B — хороший рейтинг", C: "C — средний рейтинг", D: "D — низкий рейтинг" };

export const OPERATION_NAMES = {
  LOAN_ISSUED: "Кредит выдан",
  LIMIT_OPENED: "Лимит открыт",
  TRANCHE_ISSUED: "Выдан транш",
  TRANCHE_REPAID: "Погашен транш",
  INTEREST_ACCRUED: "Начислены проценты по траншу",
  INTEREST_PAID: "Списан платёж по процентам",
  SCHEDULED_PAYMENT: "Списан плановый платёж",
  PARTIAL_REPAYMENT: "Частичное досрочное погашение",
  FULL_REPAYMENT: "Кредит погашен полностью",
};

export const CHANGE_FIELDS = { rate: "Ставка", term_months: "Срок", payment: "Платёж в месяц" };
