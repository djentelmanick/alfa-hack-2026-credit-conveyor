// Мок бэкенда с тем же интерфейсом, что и realApi. Состояние хранится в localStorage,
// заявки идут по сценариям пяти тестовых компаний, статусы меняются со временем, как на настоящем конвейере.
import { ApiError, tokenStore } from "../client.js";
import { formatMoney } from "../../lib/format.js";
import { COMPANIES, USERS, PASSWORD, PURPOSES, COLLATERAL_TYPES, PRODUCTS, RECOMMENDATIONS, REJECT_REASONS, SOURCES } from "./seed.js";
import { addDays, addMonths, advanceTo, changeTerms, contractView, ContractError, createContract, repayLoan, repayTranche, scheduleForVersion, takeTranche } from "./contracts.js";

const KEY = "ck.mockdb.v1";
const ACTIVE = ["DRAFT", "SUBMITTED", "CHECKING", "NEEDS_INFO", "SCORING", "MANUAL_REVIEW", "APPROVED", "COUNTER_OFFER", "SIGNING"];
const FINAL = ["ISSUED", "REJECTED", "CANCELLED"];

/** Финансовый профиль компаний — то, что настоящий конвейер получает из источников. */
const PROFILE = {
  "7709876543": { revenue: 24000000, score: 90, risk: "A", rate: 23.5, load: 12 },
  "503112345678": { revenue: 12000000, score: 55, risk: "B", rate: 26.5, load: 34 },
  "771234567812": { revenue: 15000000, score: 50, risk: "B", rate: 26.5, load: 13 },
  "5029123456": { revenue: 60000000, score: 100, risk: "A", rate: 23, load: 15 },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now();
const iso = (t) => new Date(t).toISOString();
const today = () => new Date().toISOString().slice(0, 10);
const fail = (status, detail, extra = {}) => { throw new ApiError(status, { detail, message: detail, ...extra }); };

let db = null;

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(db)); } catch { /* приватный режим — живём в памяти */ }
}

function load() {
  if (db) return db;
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) db = JSON.parse(raw);
  } catch { db = null; }
  if (!db) {
    db = seed();
    save();
  }
  return db;
}

/** Сбросить мок к начальному состоянию: window.__ckMockReset() в консоли. */
if (typeof window !== "undefined") window.__ckMockReset = () => { localStorage.removeItem(KEY); db = null; location.reload(); };

// ---------- сценарии ----------

const company = (inn) => COMPANIES[inn];
const userById = (id) => db.users.find((u) => u.id === id);

function push(app, to, { actor = null, reason = null, at = now() } = {}) {
  app.history.push({ at: iso(at), from_status: app.status, to_status: to, actor, reason });
  app.status = to;
  app.step_label = null;
  app.updated_at = iso(at);
}

function offer(app, fields) {
  return { id: `${app.id}-${(app.decision?.offers?.length || 0) + Math.random().toString(36).slice(2, 6)}`, product_code: app.product_code, variant: app.variant, amount: app.amount, term_months: app.term_months, ...fields };
}

function maxStatementMonths(app) {
  return app.statements.reduce((m, s) => Math.max(m, s.months_covered), 0);
}

/** План переходов после отправки: [{to, after, reason, decision, info_request}] — после, мс от отправки. */
function planFor(app, user) {
  const c = company(app.inn);
  const p = PROFILE[app.inn];
  const check = { to: "CHECKING", after: 2000 };
  const decideAt = 11000;
  if (!c || !user.inns.includes(app.inn)) {
    const reason = "По данным ЕГРЮЛ вы не руководитель компании. Подать заявку может только руководитель";
    return [check, { to: "REJECTED", after: 5000, reason: "По данным ЕГРЮЛ вы не руководитель компании", decision: { outcome: "REJECTED", reasons: [reason] } }];
  }
  if (c.status === "Ликвидирована") {
    const reason = "По данным ЕГРЮЛ компания ликвидирована";
    return [check, { to: "REJECTED", after: 5000, reason, decision: { outcome: "REJECTED", reasons: [reason] } }];
  }
  if (!app.has_account && maxStatementMonths(app) < 6) {
    const message = `Загрузите выписку за последние 6–12 месяцев: в текущей только ${maxStatementMonths(app)} ${maxStatementMonths(app) < 5 ? "месяца" : "месяцев"}`;
    return [check, { to: "NEEDS_INFO", after: 5000, reason: message, info_request: { source: "SYSTEM", title: "Нужна выписка хотя бы за 6 месяцев", items: [{ code: "STATEMENT", message }] } }];
  }
  const scoring = { to: "SCORING", after: 6000 };
  const manualReasons = [];
  if (app.variant === "SECURED") manualReasons.push("Вы выбрали кредит с залогом");
  if (app.amount > 5000000) manualReasons.push("Сумма больше 5 000 000 ₽");
  if (manualReasons.length) {
    return [check, scoring, { to: "MANUAL_REVIEW", after: decideAt, decision: { outcome: "MANUAL_REVIEW", reasons: manualReasons } }];
  }
  const limit = Math.round((p.revenue * 0.3) / 100000) * 100000;
  const expires = iso(now() + decideAt + 7 * 864e5);
  if (app.amount > limit) {
    const reason = `Сумма больше 30% вашей годовой выручки (${formatMoney(p.revenue)})`;
    return [check, scoring, {
      to: "COUNTER_OFFER", after: decideAt, reason,
      decision: { outcome: "COUNTER_OFFER", reasons: [reason], expires_at: expires, rate: p.rate, offers: [offer(app, { amount: limit, variant: "UNSECURED", rate: p.rate }), offer(app, { variant: "SECURED", rate: p.rate })] },
    }];
  }
  return [check, scoring, { to: "APPROVED", after: decideAt, decision: { outcome: "APPROVED", reasons: [], expires_at: expires, rate: p.rate, offers: [offer(app, { rate: p.rate })] } }];
}

function startPipeline(app, user, at = now()) {
  app.plan = planFor(app, user).map((s) => ({ ...s, at: at + s.after }));
  app.submitted_at = iso(at);
  app.sources = SOURCES.map((name) => ({ name, status: "OK", attempts: 1 }));
}

/** Применяет переходы, время которых уже наступило. Вызывается при каждом обращении. */
function advance(app) {
  let changed = false;
  while (app.plan && app.plan.length && app.plan[0].at <= now()) {
    const s = app.plan.shift();
    push(app, s.to, { reason: s.reason || null, at: s.at });
    if (s.decision) app.decision = { ...s.decision, decided_at: iso(s.at) };
    if (s.info_request) app.info_request = s.info_request;
    changed = true;
  }
  if (["APPROVED", "COUNTER_OFFER"].includes(app.status) && app.decision?.expires_at && new Date(app.decision.expires_at).getTime() < now()) {
    push(app, "CANCELLED", { reason: "Срок предложения истёк" });
    changed = true;
  }
  return changed;
}

function advanceAll() {
  let changed = false;
  for (const a of db.apps) changed = advance(a) || changed;
  if (changed) save();
}

// ---------- начальные данные ----------

function seed() {
  db = { users: USERS.map((u) => ({ ...u, password: PASSWORD })), apps: [], contracts: [], consents: [], signing: {}, seq: { app: 1047, contract: 155 } };
  const day = 864e5;
  const y = new Date(now() - day);
  const T = (hh, mm) => new Date(y.getFullYear(), y.getMonth(), y.getDate(), hh, mm).getTime();
  const base = (id, userId, inn, fields) => ({
    id, user_id: userId, inn, company_name: company(inn).name, purpose: "ONE_TIME_PURCHASE", product_code: "LOAN", variant: "UNSECURED",
    owner_guarantee_consent: inn.length === 10, has_account: true, collateral: null, status: "DRAFT", step_label: null,
    statements: [], info_request: null, decision: null, history: [], plan: [], sources: SOURCES.map((name) => ({ name, status: "OK", attempts: 1 })),
    contract_id: null, created_at: null, updated_at: null, submitted_at: null, ...fields,
  });
  const trail = (app, steps) => {
    app.created_at = iso(steps[0][0] - 60000);
    for (const [at, to, actor, reason] of steps) push(app, to, { at, actor, reason });
    app.submitted_at = iso(steps[0][0]);
  };
  const anna = { name: "Анна Смирнова" };

  for (const u of db.users.filter((x) => x.role === "CLIENT")) {
    for (const inn of u.inns) db.consents.push({ user_id: u.id, inn, type: "PERSONAL_DATA", given_by: u.full_name, given_at: iso(T(9, 0)) });
  }

  // №1042, Пекарня: только что отправлена — на глазах проходит проверку и оценку, потом одобрение.
  const bakery = base(1042, 1, "7709876543", { amount: 2000000, term_months: 12, created_at: iso(now() - 120000) });
  push(bakery, "SUBMITTED", { actor: anna });
  startPipeline(bakery, db.users[0]);
  db.apps.push(bakery);
  ["BKI_COMPANY", "BKI_PERSONAL"].forEach((type) => db.consents.push({ user_id: 1, inn: bakery.inn, type, given_by: "Анна Смирнова", given_at: iso(now() - 60000) }));

  const romashka = base(1043, 2, "7705123456", { amount: 1000000, term_months: 12 });
  trail(romashka, [[T(11, 20), "SUBMITTED", anna], [T(11, 21), "CHECKING"], [T(11, 21), "REJECTED", null, "По данным ЕГРЮЛ компания ликвидирована"]]);
  romashka.decision = { outcome: "REJECTED", reasons: ["По данным ЕГРЮЛ компания ликвидирована"], decided_at: iso(T(11, 21)) };
  db.apps.push(romashka);

  const sokolov = base(1044, 3, "503112345678", { amount: 4500000, term_months: 24, owner_guarantee_consent: false });
  const why = "Сумма больше 30% вашей годовой выручки (12 000 000 ₽)";
  trail(sokolov, [[T(9, 40), "SUBMITTED", anna], [T(9, 41), "CHECKING"], [T(9, 41), "SCORING"], [T(9, 43), "COUNTER_OFFER", null, why]]);
  sokolov.decision = { outcome: "COUNTER_OFFER", reasons: [why], rate: 26.5, decided_at: iso(T(9, 43)), expires_at: iso(T(9, 43) + 7 * day), offers: [offer(sokolov, { amount: 3600000, variant: "UNSECURED", rate: 26.5 }), offer(sokolov, { amount: 4500000, variant: "SECURED", rate: 26.5 })] };
  db.apps.push(sokolov);

  const ivanova = base(1045, 4, "771234567812", { amount: 3000000, term_months: 24, has_account: false, owner_guarantee_consent: false });
  const need = "Загрузите выписку за последние 6–12 месяцев: в текущей только 4 месяца";
  trail(ivanova, [[T(12, 10), "SUBMITTED", anna], [T(12, 10), "CHECKING"], [T(12, 11), "NEEDS_INFO", null, need]]);
  ivanova.statements = [statement(1, "ПАО Сбербанк", -4, 312)];
  ivanova.info_request = { source: "SYSTEM", title: "Нужна выписка хотя бы за 6 месяцев", items: [{ code: "STATEMENT", message: need }] };
  db.apps.push(ivanova);

  const techprom = base(1046, 5, "5029123456", { purpose: "SEASONAL_GAP", product_code: "CREDIT_LINE", variant: "SECURED", amount: 12000000, term_months: 60, owner_guarantee_consent: false, collateral: { type: "PRODUCTION_PREMISES", description: "Производственное помещение", estimated_value: 9000000 } });
  trail(techprom, [[T(9, 12), "SUBMITTED", anna], [T(9, 13), "CHECKING"], [T(9, 13), "SCORING"], [T(9, 15), "MANUAL_REVIEW"]]);
  techprom.decision = { outcome: "MANUAL_REVIEW", reasons: ["Вы выбрали кредит с залогом", "Сумма больше 5 000 000 ₽"], decided_at: iso(T(9, 15)) };
  db.apps.push(techprom);
  ["BKI_COMPANY", "BKI_PERSONAL"].forEach((type) => db.consents.push({ user_id: 5, inn: techprom.inn, type, given_by: "Анна Смирнова", given_at: iso(T(9, 10)) }));

  // Прошлая заявка Техпрома уже выдана — по ней открыта кредитная линия с двумя траншами.
  const past = base(1040, 5, "5029123456", { purpose: "SEASONAL_GAP", product_code: "CREDIT_LINE", variant: "SECURED", amount: 12000000, term_months: 60, owner_guarantee_consent: false, collateral: techprom.collateral });
  trail(past, [[T(8, 0) - 30 * day, "SUBMITTED", anna], [T(8, 1) - 30 * day, "CHECKING"], [T(8, 1) - 30 * day, "SCORING"], [T(8, 3) - 30 * day, "MANUAL_REVIEW"], [T(10, 0) - 29 * day, "APPROVED", { name: "Специалист банка" }], [T(10, 30) - 29 * day, "SIGNING", anna], [T(10, 32) - 29 * day, "ISSUED", anna]]);
  past.decision = { outcome: "APPROVED", reasons: [], rate: 23, decided_at: iso(T(10, 0) - 29 * day), offers: [offer(past, { rate: 23 })] };
  const start = new Date(T(10, 32) - 29 * day).toISOString().slice(0, 10);
  const line = createContract({ id: 153, number: "КЛ-2026-0153", app: past, start, actorName: "Анна Смирнова" });
  takeTranche(line, 4000000, "Анна Смирнова");
  advanceTo(line, addDays(start, 83));
  takeTranche(line, 1000000, "Анна Смирнова");
  advanceTo(line, addDays(start, 114));
  changeTerms(line, { rate: 22 }, "Специалист банка", "по обращению клиента");
  advanceTo(line, addDays(start, 142));
  past.contract_id = line.id;
  db.apps.push(past);
  db.contracts.push(line);
  return db;
}

function statement(id, bank, monthsBack, ops, months = Math.abs(monthsBack)) {
  const d = new Date();
  const to = new Date(d.getFullYear(), d.getMonth(), 0);
  const from = new Date(to.getFullYear(), to.getMonth() - months + 1, 1);
  const f = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  return { id, bank_name: bank, period_from: f(from), period_to: f(to), months_covered: months, operations_count: ops, signature_status: "VALID", inn_matches: true };
}

// ---------- представления ----------

function allowedActions(a) {
  switch (a.status) {
    case "DRAFT": return ["update", "submit", "cancel"];
    case "NEEDS_INFO": return ["update", "upload_statement", "submit"];
    case "APPROVED":
    case "COUNTER_OFFER": return ["accept", "cancel"];
    case "SIGNING": return ["sign", "cancel"];
    default: return [];
  }
}

function appView(a) {
  const c = company(a.inn);
  const egrulAnswered = !["DRAFT", "SUBMITTED"].includes(a.status) && a.history.some((h) => h.to_status === "CHECKING");
  return {
    id: a.id,
    inn: a.inn,
    company_name: c ? c.name : a.company_name,
    company: egrulAnswered && c ? { ...c } : null,
    purpose: a.purpose,
    product_code: a.product_code,
    variant: a.variant,
    amount: a.amount,
    term_months: a.term_months,
    owner_guarantee_consent: a.owner_guarantee_consent,
    has_account: a.has_account,
    collateral: a.collateral,
    status: a.status,
    step_label: a.step_label,
    is_final: FINAL.includes(a.status),
    allowed_actions: allowedActions(a),
    statements: a.statements.map((s) => ({ ...s })),
    info_request: a.status === "NEEDS_INFO" ? a.info_request : null,
    decision: a.decision && ["APPROVED", "COUNTER_OFFER", "REJECTED", "MANUAL_REVIEW", "SIGNING", "ISSUED", "CANCELLED"].includes(a.status) ? publicDecision(a) : null,
    contract_id: a.contract_id,
    created_at: a.created_at,
    updated_at: a.updated_at,
    submitted_at: a.submitted_at,
  };
}

function publicDecision(a) {
  const d = a.decision;
  return { outcome: d.outcome, reasons: d.reasons || [], offers: d.offers || [], rate: d.rate ?? null, expires_at: d.expires_at || null, decided_at: d.decided_at };
}

function historyView(a) {
  return a.history.map((h) => ({ at: h.at, from_status: h.from_status, to_status: h.to_status, actor: h.actor, reason: h.reason }));
}

// ---------- доступ ----------

function currentUser() {
  load();
  const t = tokenStore.get();
  const id = t && t.startsWith("mock-") ? Number(t.slice(5)) : null;
  const u = id && userById(id);
  if (!u) fail(401, "Войдите в кабинет");
  return u;
}

function ownApp(id) {
  const u = currentUser();
  const a = db.apps.find((x) => x.id === Number(id));
  if (!a) fail(404, "Заявка не найдена");
  if (u.role !== "ANALYST" && a.user_id !== u.id) fail(403, "Эта заявка принадлежит другой компании");
  advance(a);
  return { u, a };
}

function analyst() {
  const u = currentUser();
  if (u.role !== "ANALYST") fail(403, "Раздел только для кредитного аналитика");
  advanceAll();
  return u;
}

function ownContract(id) {
  const u = currentUser();
  const c = db.contracts.find((x) => x.id === Number(id));
  if (!c) fail(404, "Договор не найден");
  if (u.role !== "ANALYST" && c.user_id !== u.id) fail(403, "Этот договор принадлежит другой компании");
  return { u, c };
}

const actorName = (u) => (u.role === "ANALYST" ? "Специалист банка" : u.full_name);

function variantOf(code, variant) {
  return PRODUCTS.find((p) => p.code === code)?.variants.find((v) => v.code === variant);
}

function validate(a) {
  const errors = [];
  const e = (field, message) => errors.push({ field, message });
  if (!/^(\d{10}|\d{12})$/.test(a.inn || "")) e("inn", "Укажите 10 цифр для компании или 12 — для ИП");
  const v = variantOf(a.product_code, a.variant);
  if (!v) e("variant", "Выберите вариант");
  else {
    if (!a.amount || a.amount < v.amount_min || a.amount > v.amount_max) e("amount", `Укажите сумму от ${formatMoney(v.amount_min)} до ${formatMoney(v.amount_max)}`);
    if (!a.term_months || a.term_months < v.term_min || a.term_months > v.term_max) e("term_months", `Укажите срок от ${v.term_min} до ${v.term_max} месяцев`);
    if (v.collateral_required) {
      const c = a.collateral || {};
      if (!c.type) e("collateral.type", "Выберите тип залога");
      if (!c.description) e("collateral.description", "Опишите залог");
      const min = Math.ceil(a.amount / 2);
      if (!c.estimated_value || c.estimated_value < min) e("collateral.estimated_value", `Залог должен стоить не меньше ${formatMoney(min)} — половины суммы`);
    }
  }
  if (a.has_account === false && !a.statements.length) e("statements", "Загрузите выписку по счёту в другом банке");
  return errors;
}

function wrapContract(fn) {
  try { return fn(); } catch (err) {
    if (err instanceof ContractError) fail(err.status, err.message);
    throw err;
  }
}

async function respond(fn, ms = 250) {
  await sleep(ms);
  const result = fn();
  save();
  return JSON.parse(JSON.stringify(result ?? null));
}

// ---------- API ----------

export const mockApi = {
  login: (login, password) => respond(() => {
    load();
    const u = db.users.find((x) => x.login === String(login).trim());
    if (!u || u.password !== password) fail(401, "Неверный логин или пароль");
    tokenStore.set("mock-" + u.id);
    return { access_token: "mock-" + u.id, user: { id: u.id, role: u.role, full_name: u.full_name } };
  }),

  register: (body) => respond(() => {
    load();
    if (db.users.some((u) => u.login === body.login)) fail(409, "Логин занят", { errors: [{ field: "login", message: "Логин занят" }] });
    const p = body.passport || {};
    const issued = /^\d{2}\.\d{2}\.\d{4}$/.test(p.issued_at || "") ? p.issued_at.split(".").reverse().join("-") : p.issued_at;
    const okDate = issued && !Number.isNaN(new Date(issued).getTime()) && issued <= today();
    if (!/^\d{4}$/.test(p.series || "") || !/^\d{6}$/.test(p.number || "") || !okDate || p.series === "0000") fail(422, "Паспорт недействителен — проверьте серию, номер и дату выдачи", { errors: [{ field: "passport", message: "Паспорт недействителен — проверьте серию, номер и дату выдачи" }] });
    const u = { id: Math.max(...db.users.map((x) => x.id)) + 1, login: body.login, password: body.password, full_name: body.full_name, role: "CLIENT", inns: [], has_account: true };
    db.users.push(u);
    return { id: u.id, login: u.login, full_name: u.full_name, role: u.role };
  }),

  me: () => respond(() => {
    const u = currentUser();
    return { id: u.id, login: u.login, full_name: u.full_name, role: u.role, companies: u.inns.map((inn) => ({ id: inn, inn, name: company(inn).name, has_account: u.has_account })) };
  }, 100),

  purposes: () => respond(() => PURPOSES, 100),
  collateralTypes: () => respond(() => COLLATERAL_TYPES, 100),
  products: () => respond(() => PRODUCTS, 100),
  recommendation: (purpose) => respond(() => RECOMMENDATIONS[purpose] || RECOMMENDATIONS.ONE_TIME_PURCHASE, 150),
  analystRejectReasons: () => respond(() => REJECT_REASONS, 100),

  applications: (query = {}) => respond(() => {
    const u = currentUser();
    advanceAll();
    const items = db.apps.filter((a) => a.user_id === u.id && (!query.inn || a.inn === query.inn)).sort((a, b) => b.id - a.id).map(appView);
    return { items, total: items.length };
  }),

  application: (id) => respond(() => appView(ownApp(id).a)),
  history: (id) => respond(() => historyView(ownApp(id).a)),
  decision: (id) => respond(() => {
    const { a } = ownApp(id);
    if (!a.decision) fail(404, "Решения ещё нет");
    return publicDecision(a);
  }),

  createApplication: (body) => respond(() => {
    const u = currentUser();
    advanceAll();
    const dup = db.apps.find((a) => a.inn === body.inn && ACTIVE.includes(a.status) && a.status !== "DRAFT");
    if (dup) fail(409, `По этой компании уже есть заявка №${dup.id}. Новую можно подать, когда она завершится`, { application_id: dup.id });
    const c = company(body.inn);
    const a = {
      id: db.seq.app++, user_id: u.id, inn: body.inn, company_name: c ? c.name : `ИНН ${body.inn}`,
      purpose: body.purpose, product_code: body.product_code, variant: body.variant, amount: Number(body.amount) || 0, term_months: Number(body.term_months) || 0,
      owner_guarantee_consent: !!body.owner_guarantee_consent, has_account: body.has_account ?? true, collateral: body.collateral || null,
      status: "DRAFT", step_label: null, statements: [], info_request: null, decision: null, history: [], plan: [], sources: [], contract_id: null,
      created_at: iso(now()), updated_at: iso(now()), submitted_at: null,
    };
    db.apps.push(a);
    return appView(a);
  }),

  updateApplication: (id, body) => respond(() => {
    const { a } = ownApp(id);
    if (!["DRAFT", "NEEDS_INFO"].includes(a.status)) fail(409, "Заявку сейчас нельзя изменить");
    const fields = ["purpose", "product_code", "variant", "amount", "term_months", "owner_guarantee_consent", "has_account", "collateral"];
    if (a.status === "DRAFT") fields.push("inn");
    for (const f of fields) if (body[f] !== undefined) a[f] = ["amount", "term_months"].includes(f) ? Number(body[f]) || 0 : body[f];
    if (body.inn && company(body.inn)) a.company_name = company(body.inn).name;
    a.updated_at = iso(now());
    return appView(a);
  }),

  validateApplication: (id) => respond(() => ({ errors: validate(ownApp(id).a) })),

  submitApplication: (id) => respond(() => {
    const { u, a } = ownApp(id);
    if (!["DRAFT", "NEEDS_INFO"].includes(a.status)) fail(409, "Заявка уже отправлена");
    const errors = validate(a);
    if (errors.length) fail(422, "В заявке есть ошибки", { errors });
    if (a.status === "DRAFT") {
      const given = db.consents.filter((c) => c.user_id === u.id && c.inn === a.inn).map((c) => c.type);
      const need = ["PERSONAL_DATA", "BKI_PERSONAL", ...(a.inn.length === 10 ? ["BKI_COMPANY"] : [])].filter((t) => !given.includes(t));
      if (need.length) fail(422, "Дайте согласия на запрос кредитной истории", { errors: [{ field: "consents", message: "Дайте согласия на запрос кредитной истории" }] });
    }
    const fromAnalyst = a.info_request?.source === "ANALYST";
    push(a, "SUBMITTED", { actor: { name: u.full_name } });
    a.info_request = null;
    if (fromAnalyst) {
      const t = now();
      a.plan = [{ to: "CHECKING", at: t + 2000 }, { to: "SCORING", at: t + 5000 }, { to: "MANUAL_REVIEW", at: t + 8000, decision: a.decision }];
    } else startPipeline(a, u);
    return { id: a.id, status: a.status };
  }),

  uploadStatement: (id, file) => (async () => {
    const text = await file.text().catch(() => "");
    return respond(() => {
      const { a } = ownApp(id);
      if (!["DRAFT", "NEEDS_INFO"].includes(a.status)) fail(409, "Выписку сейчас нельзя загрузить");
      const otherInn = (file.name.match(/\d{10,12}/) || [])[0];
      if (otherInn && otherInn !== a.inn) fail(422, `Выписка по счёту другой компании: ИНН ${otherInn}, а в заявке ${a.inn}`, { inn_matches: false });
      const dates = [...text.matchAll(/Дата(?:Начала|Конца)=(\d{2})\.(\d{2})\.(\d{4})/g)].map((m) => `${m[3]}-${m[2]}-${m[1]}`);
      const ops = (text.match(/СекцияДокумент=/g) || []).length;
      const bank = (/Отправитель=(.+)/.exec(text) || [])[1]?.trim();
      let s;
      if (dates.length >= 2) {
        const [from, to] = dates;
        const months = Math.max(1, Math.round((new Date(to) - new Date(from)) / (30.44 * 864e5)));
        s = { id: Date.now(), bank_name: bank || "ПАО Сбербанк", period_from: from, period_to: to, months_covered: months, operations_count: ops, signature_status: "VALID", inn_matches: true };
      } else {
        const months = a.statements.length || a.status === "NEEDS_INFO" ? 12 : 4;
        s = statement(Date.now(), bank || "ПАО Сбербанк", -months, months * 78, months);
      }
      a.statements.push(s);
      return s;
    }, 900);
  })(),

  deleteStatement: (id, statementId) => respond(() => {
    const { a } = ownApp(id);
    a.statements = a.statements.filter((s) => s.id !== Number(statementId));
    return null;
  }),

  accept: (id, offerId) => respond(() => {
    const { u, a } = ownApp(id);
    if (!["APPROVED", "COUNTER_OFFER"].includes(a.status)) fail(409, "Предложение уже недоступно");
    if (a.decision.expires_at && new Date(a.decision.expires_at).getTime() < now()) fail(410, `Срок предложения истёк. Подайте новую заявку`);
    const o = (a.decision.offers || []).find((x) => x.id === offerId) || a.decision.offers[0];
    a.amount = o.amount;
    a.term_months = o.term_months;
    a.product_code = o.product_code;
    if (o.variant === "SECURED" && a.variant !== "SECURED") {
      a.variant = "SECURED";
      push(a, "NEEDS_INFO", { actor: { name: u.full_name }, reason: "Клиент выбрал вариант с залогом" });
      a.info_request = { source: "SYSTEM", title: "Опишите залог", items: [{ code: "COLLATERAL", message: `Укажите залог: его оценочная стоимость — не меньше ${formatMoney(Math.ceil(a.amount / 2))}` }] };
      return appView(a);
    }
    a.decision.rate = o.rate;
    push(a, "SIGNING", { actor: { name: u.full_name } });
    return appView(a);
  }),

  cancel: (id) => respond(() => {
    const { u, a } = ownApp(id);
    if (!ACTIVE.includes(a.status)) fail(409, "Заявку уже нельзя отменить");
    push(a, "CANCELLED", { actor: { name: u.full_name }, reason: "Клиент отказался" });
    a.plan = [];
    return appView(a);
  }),

  signingCode: (id) => respond(() => {
    const { a } = ownApp(id);
    if (a.status !== "SIGNING") fail(409, "Подписание недоступно");
    const code = String(Math.floor(100000 + Math.random() * 900000));
    const s = { code, expires_at: iso(now() + 5 * 60000) };
    db.signing[a.id] = s;
    return s;
  }),

  signingConfirm: (id, code) => respond(() => {
    const { u, a } = ownApp(id);
    const s = db.signing[a.id];
    if (a.status !== "SIGNING") fail(409, "Подписание недоступно");
    if (!s || s.code !== String(code) || new Date(s.expires_at).getTime() < now()) fail(400, "Код неверный или устарел");
    delete db.signing[a.id];
    push(a, "ISSUED", { actor: { name: u.full_name } });
    const id2 = db.seq.contract++;
    const prefix = a.product_code === "CREDIT_LINE" ? "КЛ" : "КР";
    const c = createContract({ id: id2, number: `${prefix}-${new Date().getFullYear()}-${String(id2).padStart(4, "0")}`, app: a, start: today(), actorName: u.full_name });
    db.contracts.push(c);
    a.contract_id = c.id;
    return { status: "ISSUED", contract_id: c.id };
  }),

  consents: (inn) => respond(() => {
    const u = currentUser();
    return db.consents.filter((c) => c.user_id === u.id && c.inn === inn).map(({ type, given_by, given_at }) => ({ type, given_by, given_at }));
  }, 120),

  giveConsents: (inn, types) => respond(() => {
    const u = currentUser();
    for (const type of types) {
      if (!db.consents.some((c) => c.user_id === u.id && c.inn === inn && c.type === type)) db.consents.push({ user_id: u.id, inn, type, given_by: u.full_name, given_at: iso(now()) });
    }
    return null;
  }),

  subscribeStatus(id, onMessage) {
    let last = null;
    const tick = () => {
      try {
        const { a } = ownApp(id);
        save();
        const key = a.status + "|" + a.step_label;
        if (key !== last) {
          last = key;
          onMessage({ status: a.status, step_label: a.step_label, expires_at: a.decision?.expires_at || null });
        }
      } catch { /* нет доступа — молчим, страница покажет ошибку сама */ }
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  },

  // ---------- договоры ----------
  contracts: () => respond(() => {
    const u = currentUser();
    return db.contracts.filter((c) => u.role === "ANALYST" || c.user_id === u.id).map(contractView);
  }),
  contract: (id) => respond(() => contractView(ownContract(id).c)),
  schedule: (id, version) => respond(() => scheduleForVersion(ownContract(id).c, version)),
  operations: (id) => respond(() => [...ownContract(id).c.operations].reverse()),
  versions: (id) => respond(() => ownContract(id).c.versions.map(({ schedule, ...v }) => v).reverse()),

  takeTranche: (id, amount) => respond(() => {
    const { u, c } = ownContract(id);
    return wrapContract(() => takeTranche(c, Number(amount), u.full_name));
  }),
  repayTranche: (id, trancheId, amount) => respond(() => {
    const { u, c } = ownContract(id);
    wrapContract(() => repayTranche(c, trancheId, Number(amount), u.full_name));
    return contractView(c);
  }),
  repay: (id, kind, amount) => respond(() => {
    const { u, c } = ownContract(id);
    wrapContract(() => repayLoan(c, kind, Number(amount), u.full_name));
    return contractView(c);
  }),
  emulateNextMonth: (id) => respond(() => {
    const { c } = ownContract(id);
    if (c.status === "CLOSED") fail(409, "Договор закрыт");
    const operations = advanceTo(c, addMonths(c.contract_date, 1));
    return { contract_date: c.contract_date, operations };
  }),
  emulateRewind: (id, target) => respond(() => {
    const { c } = ownContract(id);
    if (c.status === "CLOSED") fail(409, "Договор закрыт");
    if (!target || target <= c.contract_date) fail(422, "Дата должна быть позже текущей даты договора", { errors: [{ field: "target_date", message: "Дата должна быть позже текущей даты договора" }] });
    const operations = advanceTo(c, target);
    return { contract_date: c.contract_date, operations };
  }),
  emulateReset: (id) => respond(() => {
    const { c } = ownContract(id);
    const fresh = { ...JSON.parse(c.initial), initial: c.initial };
    Object.keys(c).forEach((k) => delete c[k]);
    Object.assign(c, fresh);
    return contractView(c);
  }),

  // ---------- аналитик ----------
  analystApplications: (query = {}) => respond(() => {
    analyst();
    let list = db.apps.filter((a) => a.status !== "DRAFT");
    if (query.queue === "manual") list = list.filter((a) => a.status === "MANUAL_REVIEW");
    if (query.inn) list = list.filter((a) => a.inn.includes(query.inn));
    if (query.status) list = list.filter((a) => a.status === query.status);
    if (query.risk_class) list = list.filter((a) => PROFILE[a.inn]?.risk === query.risk_class && !["REJECTED"].includes(a.status));
    list.sort((a, b) => (a.submitted_at < b.submitted_at ? -1 : 1));
    const size = Number(query.size) || 20;
    const page = Number(query.page) || 1;
    const items = list.slice((page - 1) * size, page * size).map((a) => ({
      id: a.id, company_name: company(a.inn)?.name || a.company_name, inn: a.inn, product_code: a.product_code, variant: a.variant, amount: a.amount, status: a.status,
      risk_class: a.status === "REJECTED" || !PROFILE[a.inn] ? null : PROFILE[a.inn].risk,
      manual_reasons: a.status === "MANUAL_REVIEW" ? [a.variant === "SECURED" && "С залогом", a.amount > 5000000 && "Больше 5 млн ₽"].filter(Boolean) : [],
      submitted_at: a.submitted_at,
    }));
    return { items, total: list.length, page, size };
  }),

  analystReport: (id) => respond(() => {
    analyst();
    const a = db.apps.find((x) => x.id === Number(id));
    if (!a) fail(404, "Заявка не найдена");
    advance(a);
    const consents = db.consents.filter((c) => c.user_id === a.user_id && c.inn === a.inn);
    const p = PROFILE[a.inn];
    const comparisons = [];
    if (a.collateral) comparisons.push({ check: "Покрытие залогом", what: `${formatMoney(a.collateral.estimated_value)} ↔ 50% суммы`, values: `${Math.round((a.collateral.estimated_value / a.amount) * 100)}%`, passed: a.collateral.estimated_value >= a.amount / 2 });
    for (const s of a.statements) comparisons.push({ check: "ИНН в выписке", what: "выписка ↔ заявка", values: s.inn_matches ? "совпадает" : "не совпадает", passed: s.inn_matches });
    const scored = p && !["REJECTED"].includes(a.status) && a.history.some((h) => h.to_status === "SCORING");
    const stopOk = a.sources.every((s) => s.status === "OK");
    return {
      application: appView(a),
      provided: {
        questionnaire: { purpose: PURPOSES.find((x) => x.code === a.purpose)?.label, product_code: a.product_code, variant: a.variant, amount: a.amount, term_months: a.term_months },
        applicant: { full_name: userById(a.user_id)?.full_name, passport_verified: true },
        consents: consents.map(({ type, given_at }) => ({ type, given_at })),
        collateral: a.collateral,
      },
      sources: a.sources.length ? a.sources : SOURCES.map((name) => ({ name, status: "OK", attempts: 1 })),
      comparisons,
      scoring: scored ? { total: p.score, max: 100, risk_class: p.risk, debt_load_percent: p.load } : null,
      decision: { summary: a.status === "MANUAL_REVIEW" ? `Ручная проверка: ${(a.decision?.reasons || []).map((r) => r.replace("Вы выбрали кредит", "вариант").toLowerCase()).join("; ")}` : (a.decision?.reasons || []).join("; ") || null, recommended_action: stopOk ? "APPROVE" : "RECHECK" },
      history: historyView(a),
      contract: a.contract_id ? { id: a.contract_id, number: db.contracts.find((c) => c.id === a.contract_id)?.number } : null,
      can_approve: stopOk,
    };
  }),

  analystApprove: (id, body) => respond(() => {
    const u = analyst();
    const a = manualApp(id);
    if (!a.sources.every((s) => s.status === "OK")) fail(409, "Одобрить можно, когда ответят все источники стоп-факторов");
    const rate = Number(String(body.rate).replace(",", "."));
    if (!(rate >= 23 && rate <= 29)) fail(422, "Ставка — от 23 до 29%", { errors: [{ field: "rate", message: "Укажите ставку от 23 до 29%" }] });
    push(a, "APPROVED", { actor: { name: actorName(u) }, reason: body.comment || null });
    a.decision = { outcome: "APPROVED", reasons: body.comment ? [body.comment] : [], rate, offers: [offer(a, { rate })], decided_at: iso(now()), expires_at: iso(now() + 7 * 864e5) };
    return appView(a);
  }),
  analystCounterOffer: (id, body) => respond(() => {
    const u = analyst();
    const a = manualApp(id);
    const options = (body.options || []).filter((o) => o.amount);
    if (!options.length) fail(422, "Добавьте хотя бы один вариант", { errors: [{ field: "options", message: "Добавьте хотя бы один вариант" }] });
    push(a, "COUNTER_OFFER", { actor: { name: actorName(u) }, reason: body.comment || null });
    a.decision = { outcome: "COUNTER_OFFER", reasons: body.comment ? [body.comment] : [], rate: Number(options[0].rate), offers: options.map((o) => offer(a, { ...o, amount: Number(o.amount), term_months: Number(o.term_months), rate: Number(o.rate) })), decided_at: iso(now()), expires_at: iso(now() + 7 * 864e5) };
    return appView(a);
  }),
  analystRequestInfo: (id, body) => respond(() => {
    const u = analyst();
    const a = manualApp(id);
    const items = (body.items || []).filter((i) => i.code);
    if (!items.length) fail(422, "Выберите, что запросить", { errors: [{ field: "items", message: "Выберите, что запросить" }] });
    const titles = { COLLATERAL: "Специалист просит уточнить залог", STATEMENT: "Специалист просит загрузить выписку" };
    const reason = items.map((i) => i.message).filter(Boolean).join(". ") || body.comment || null;
    push(a, "NEEDS_INFO", { actor: { name: actorName(u) }, reason });
    a.info_request = { source: "ANALYST", title: titles[items[0].code] || "Специалисту не хватает данных", items: items.map((i) => ({ code: i.code, message: i.message || titles[i.code] })) };
    return appView(a);
  }),
  analystReject: (id, body) => respond(() => {
    const u = analyst();
    const a = manualApp(id);
    const r = REJECT_REASONS.find((x) => x.code === body.reason_code);
    if (!r) fail(422, "Выберите причину", { errors: [{ field: "reason_code", message: "Выберите причину" }] });
    const reason = r.code === "OTHER" ? body.comment || r.label : r.label;
    push(a, "REJECTED", { actor: { name: actorName(u) }, reason });
    a.decision = { outcome: "REJECTED", reasons: [reason], decided_at: iso(now()) };
    return appView(a);
  }),
  analystRecheck: (id, body) => respond(() => {
    const u = analyst();
    const a = manualApp(id);
    push(a, "CHECKING", { actor: { name: actorName(u) }, reason: body.comment || null });
    a.sources = SOURCES.map((name) => ({ name, status: "OK", attempts: 1 }));
    a.plan = [{ to: "SCORING", at: now() + 3000 }, { to: "MANUAL_REVIEW", at: now() + 6000 }];
    return appView(a);
  }),
  analystUpdateContract: (id, body) => respond(() => {
    const u = analyst();
    const { c } = ownContract(id);
    if (!body.comment) fail(422, "Напишите комментарий — его увидит клиент", { errors: [{ field: "comment", message: "Напишите комментарий — его увидит клиент" }] });
    if (body.amount !== undefined) fail(422, "Увеличить сумму можно только новой заявкой");
    wrapContract(() => changeTerms(c, { rate: body.rate, term_months: body.term_months }, actorName(u), body.comment));
    return contractView(c);
  }),
};

function manualApp(id) {
  const a = db.apps.find((x) => x.id === Number(id));
  if (!a) fail(404, "Заявка не найдена");
  advance(a);
  if (a.status !== "MANUAL_REVIEW") fail(409, "Заявка уже не на ручной проверке");
  return a;
}
