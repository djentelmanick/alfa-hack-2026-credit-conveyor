import { formatMoney } from "./format.js";

export const PRODUCT_NAMES = { LUMP_SUM: "Кредит одной суммой", REVOLVING_LINE: "Кредитная линия" };
export const isCreditLine = (code) => code === "REVOLVING_LINE";
export const VARIANT_NAMES = { UNSECURED: "без залога", SECURED: "с залогом" };

export const productName = (code) => PRODUCT_NAMES[code] || code;
export const variantName = (code) => VARIANT_NAMES[code] || code;

export function productLine(app, { term = true } = {}) {
  const parts = [productName(app.product_code), variantName(app.variant), formatMoney(app.amount)];
  if (term && app.term_months) parts.push(`${app.term_months} мес`);
  return parts.join(" · ");
}

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

export const shortId = (id) => String(id || "").slice(0, 8);

// Роли из кейса; SOLE_TRADER выставляет сервер для ИП и в выборе не показывается.
export const APPLICANT_ROLES = [
  ["DIRECTOR", "Генеральный директор"],
  ["OWNER", "Собственник бизнеса"],
  ["FINANCE_OFFICER", "Финансовый директор или бухгалтер"],
  ["FUNDING_OFFICER", "Сотрудник, отвечающий за финансирование"],
  ["BANKING_OFFICER", "Сотрудник, работающий с банковскими продуктами"],
  ["OTHER_AUTHORIZED", "Иной уполномоченный сотрудник"],
];
export const roleName = (code) => (code === "SOLE_TRADER" ? "Индивидуальный предприниматель" : Object.fromEntries(APPLICANT_ROLES)[code] || code);
export const isRepresentative = (authority) => authority?.basis === "POWER_OF_ATTORNEY";

// Личные согласия директора и собственника представитель только подтверждает: дать их за другого человека нельзя.
export const REPRESENTATIVE_CONSENT_LABELS = {
  BKI_PERSONAL: "Подтверждаю, что согласие руководителя на запрос его кредитной истории получено",
};

export const CONSENT_LABELS = {
  PERSONAL_DATA: "Согласие на обработку персональных данных",
  BKI_COMPANY: "Согласие на запрос кредитной истории компании",
  BKI_PERSONAL: "Согласие на запрос моей кредитной истории",
};

export const isSoleTrader = (inn) => String(inn || "").length === 12;

export const RISK_LABELS = { A: "A — высокий рейтинг", B: "B — хороший рейтинг", C: "C — средний рейтинг", D: "D — низкий рейтинг" };

export const OPERATION_NAMES = {
  DISBURSEMENT: "Кредит выдан",
  LIMIT_OPENED: "Лимит открыт",
  TRANCHE_DRAWN: "Выдан транш",
  TRANCHE_REPAID: "Погашен транш",
  INTEREST_ACCRUED: "Начислены проценты по траншу",
  SCHEDULED_PAYMENT: "Списан плановый платёж",
  PARTIAL_PREPAYMENT: "Частичное досрочное погашение",
  FULL_REPAYMENT: "Кредит погашен полностью",
  CONTRACT_EDITED: "Изменены условия договора",
};

export const CHANGE_FIELDS = { rate: "Ставка", term_months: "Срок", payment: "Платёж в месяц", principal: "Сумма", outstanding: "Остаток долга" };

export const SOURCE_NAMES = {
  EGRUL: "ЕГРЮЛ / ЕГРИП",
  BKI: "Бюро кредитных историй",
  TURNOVER: "Обороты по счёту",
  FNS: "ФНС «Прозрачный бизнес»",
  GIR_BO: "ГИР БО — бухотчётность",
  FSSP: "ФССП — долги у приставов",
  CBR_KYC: "ЦБ — знай своего клиента",
  ROSFIN: "Росфинмониторинг",
  BANK_SIGNATURE: "Подпись банка в выписке",
};

export const COMPANY_STATUS = { ACTIVE: "Действует", LIQUIDATED: "Ликвидирована", BANKRUPT: "Банкрот", REORGANIZING: "Реорганизация" };

export const OUTCOME_NAMES = { APPROVED: "Одобрить", COUNTER_OFFER: "Предложить другие условия", NEEDS_INFO: "Запросить данные", MANUAL_REVIEW: "Решение за специалистом", REJECTED: "Отказать" };

export const CHECK_OUTCOMES = { PASSED: "Прошла", FAILED: "Не прошла", STOP_FACTOR: "Стоп-фактор", NO_DATA: "Нет данных" };

export const REJECT_REASONS = {
  STOP_FACTOR: "Стоп-фактор в проверках",
  LOW_SCORE: "Низкий скоринговый балл",
  HIGH_DEBT_LOAD: "Высокая долговая нагрузка",
  LOW_REVENUE: "Выручки не хватает на платежи",
  BAD_CREDIT_HISTORY: "Плохая кредитная история",
  INSUFFICIENT_COLLATERAL: "Залога недостаточно",
  OTHER: "Другое",
};

export const REASON_LABELS = {
  ...REJECT_REASONS,
  SECURED_VARIANT: "Кредит с залогом",
  STATEMENT_UNVERIFIED: "Выписка без подтверждённой подписи банка",
  AMOUNT_OVER_5M: "Сумма больше 5 млн ₽",
  RECENT_OWNER_CHANGE: "Недавно сменился владелец",
  UNRELIABLE_REGISTRY_RECORD: "Недостоверные сведения в ЕГРЮЛ",
  CBR_YELLOW_LEVEL: "Жёлтый уровень риска ЦБ",
  TAX_DEBT_OVER_10K: "Налоговый долг больше 10 000 ₽",
  DIRECTOR_BAILIFF_OVER_10K: "Долг руководителя у приставов больше 10 000 ₽",
  NO_COUNTER_OFFER_AVAILABLE: "Нечего предложить взамен",
  SCORING_NOT_IMPLEMENTED: "Скоринг пока не подключён",
  SOURCE_UNAVAILABLE: "Источник данных не ответил",
  COMPANY_INACTIVE: "Компания не действует по ЕГРЮЛ",
  BUSINESS_AGE_UNDER_6M: "Бизнесу меньше 6 месяцев",
  CURRENT_OVERDUE_OVER_30: "Текущая просрочка больше 30 дней",
  FORBIDDEN_OKVED: "Вид деятельности, который банк не кредитует",
  TAX_BLOCK_OR_BAILIFF: "Блокировка счёта налоговой или долг у приставов",
  CBR_RED_LEVEL: "Красный уровень риска ЦБ",
  ROSFIN_LIST: "Компания в перечне Росфинмониторинга",
  DIRECTOR_DISQUALIFIED: "Руководитель дисквалифицирован",
  MANUAL_REVIEW: "Ручная проверка",
  CLASS_D_REJECT: "Отказ: класс риска D",
  REVENUE_LIMIT_COUNTER_OFFER: "Сумма выше лимита от выручки",
  CLASS_A_APPROVE: "Одобрение: класс риска A",
  CLASS_B_APPROVE: "Одобрение: класс риска B",
  CLASS_C_COUNTER_OFFER: "Контрпредложение: класс риска C",
  ANALYST_DECISION: "Решение специалиста",
  PIPELINE_STARTED: "Начали проверку",
  CHECKS_PASSED: "Проверки пройдены",
  STATEMENT_PERIOD: "Выписка за короткий период",
  OFFER_EXPIRED: "Срок предложения истёк",
};

export function reasonLabel(text) {
  if (!text) return text;
  const parts = String(text).split(/\s*,\s*/);
  if (!parts.every((p) => /^[A-Z][A-Z0-9_]*$/.test(p))) return text;
  return parts.map((p) => REASON_LABELS[p] || p).join(", ");
}
