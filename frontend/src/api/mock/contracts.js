// Упрощённая модель договора для мока: даты договора, плановые платежи, транши, версии.
// Настоящие расчёты делает бэкенд; здесь только то, что нужно, чтобы страница договора жила без него.
import { formatDate, formatMoney } from "../../lib/format.js";

const r0 = (n) => Math.round(n);

export function addMonths(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const target = new Date(Date.UTC(y, m - 1 + n, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(d, last));
  return target.toISOString().slice(0, 10);
}

export function addDays(iso, n) {
  const t = new Date(iso + "T00:00:00Z");
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

function annuity(principal, rate, months) {
  if (months <= 0) return principal;
  const i = rate / 100 / 12;
  return Math.round(((principal * i) / (1 - Math.pow(1 + i, -months))) / 100) * 100;
}

const isLine = (c) => c.product_code === "CREDIT_LINE";

function paymentDates(c) {
  return Array.from({ length: c.term_months }, (_, k) => addMonths(c.start_date, k + 1));
}

function op(c, type, amount, actor, date = c.contract_date) {
  const o = { id: c.operations.length + 1, contract_date: date, type, amount, actor, balance_after: c.outstanding };
  c.operations.push(o);
  return o;
}

function lineAvailable(c) {
  return c.limit - c.outstanding;
}

/** Будущие платежи от даты договора. Для линии — проценты по долгу и возврат траншей в срок. */
export function computeSchedule(c) {
  const dates = paymentDates(c);
  const future = dates.filter((d) => d > c.contract_date);
  const paidCount = dates.length - future.length;
  if (isLine(c)) {
    let balance = c.outstanding;
    const tranches = c.tranches.filter((t) => t.outstanding > 0).map((t) => ({ ...t }));
    return future.slice(0, 12).map((due, i) => {
      const interest = r0((balance * c.rate) / 100 / 12);
      const principal = tranches.filter((t) => t.due_at <= due && t.outstanding > 0).reduce((s, t) => { const p = t.outstanding; t.outstanding = 0; return s + p; }, 0);
      balance -= principal;
      return { n: paidCount + i + 1, due_date: due, payment: interest + principal, principal, interest, balance };
    });
  }
  let balance = c.outstanding;
  const payment = c.payment;
  return future.map((due, i) => {
    const interest = r0((balance * c.rate) / 100 / 12);
    const last = i === future.length - 1;
    const principal = last ? balance : Math.min(balance, payment - interest);
    balance -= principal;
    return { n: paidCount + i + 1, due_date: due, payment: principal + interest, principal, interest, balance };
  });
}

function processPaymentDate(c, date) {
  if (c.status === "CLOSED") return [];
  const out = [];
  if (isLine(c)) {
    if (c.outstanding > 0) {
      const interest = r0((c.outstanding * c.rate) / 100 / 12);
      out.push(op(c, "INTEREST_ACCRUED", interest, "Система", date));
      out.push(op(c, "INTEREST_PAID", interest, "Система", date));
    }
    for (const t of c.tranches) {
      if (t.outstanding > 0 && t.due_at <= date) {
        const amount = t.outstanding;
        t.outstanding = 0;
        c.outstanding -= amount;
        out.push(op(c, "TRANCHE_REPAID", amount, "Система", date));
      }
    }
    return out;
  }
  const interest = r0((c.outstanding * c.rate) / 100 / 12);
  const remaining = paymentDates(c).filter((d) => d >= date).length;
  const principal = remaining <= 1 ? c.outstanding : Math.min(c.outstanding, c.payment - interest);
  c.outstanding -= principal;
  out.push(op(c, "SCHEDULED_PAYMENT", principal + interest, "Система", date));
  if (c.outstanding <= 0) {
    c.outstanding = 0;
    c.status = "CLOSED";
  }
  return out;
}

/** Сдвигает дату договора, проводя все плановые операции по пути. */
export function advanceTo(c, target) {
  const due = paymentDates(c).filter((d) => d > c.contract_date && d <= target);
  const ops = [];
  for (const d of due) {
    c.contract_date = d;
    ops.push(...processPaymentDate(c, d));
  }
  c.contract_date = target;
  return ops;
}

function freezeCurrentSchedule(c) {
  const v = c.versions[c.versions.length - 1];
  v.schedule = computeSchedule(c);
}

export function addVersion(c, { changes, author, reason }) {
  freezeCurrentSchedule(c);
  c.versions.push({ version_no: c.versions.length + 1, changes, author, reason, created_at: c.contract_date });
}

export function createContract({ id, number, app, start, actorName }) {
  const line = app.product_code === "CREDIT_LINE";
  const c = {
    id,
    number,
    application_id: app.id,
    user_id: app.user_id,
    inn: app.inn,
    company_name: app.company_name,
    product_code: app.product_code,
    variant: app.variant,
    status: "ACTIVE",
    rate: app.decision.rate,
    term_months: app.term_months,
    start_date: start,
    contract_date: start,
    limit: line ? app.amount : null,
    amount: line ? null : app.amount,
    outstanding: line ? 0 : app.amount,
    payment: line ? null : annuity(app.amount, app.decision.rate, app.term_months),
    tranche_min: line ? 50000 : null,
    tranche_window_end: line ? addMonths(start, Math.max(app.term_months - 12, 1)) : null,
    tranches: [],
    operations: [],
    versions: [{ version_no: 1, changes: [], author: actorName, reason: "Договор подписан", created_at: start }],
  };
  op(c, line ? "LIMIT_OPENED" : "LOAN_ISSUED", app.amount, "Система");
  c.initial = JSON.stringify({ ...c, initial: undefined });
  return c;
}

export class ContractError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function takeTranche(c, amount, actor) {
  if (c.status === "CLOSED") throw new ContractError(409, "Договор закрыт");
  if (c.contract_date > c.tranche_window_end) throw new ContractError(422, `Окно выборки закрыто: транши можно было брать до ${formatDate(c.tranche_window_end)}`);
  if (amount < c.tranche_min) throw new ContractError(422, `Транш — не меньше ${formatMoney(c.tranche_min)}`);
  if (amount > lineAvailable(c)) throw new ContractError(422, "Сумма больше доступного лимита");
  const t = { id: c.tranches.length + 1, amount, issued_at: c.contract_date, due_at: addMonths(c.contract_date, 12), outstanding: amount };
  c.tranches.push(t);
  c.outstanding += amount;
  op(c, "TRANCHE_ISSUED", amount, actor);
  return t;
}

export function repayTranche(c, trancheId, amount, actor) {
  if (c.status === "CLOSED") throw new ContractError(409, "Договор закрыт");
  const t = c.tranches.find((x) => x.id === Number(trancheId));
  if (!t) throw new ContractError(404, "Транш не найден");
  if (amount <= 0) throw new ContractError(422, "Укажите сумму больше нуля");
  if (amount > t.outstanding) throw new ContractError(422, `Сумма больше долга по траншу`);
  t.outstanding -= amount;
  c.outstanding -= amount;
  op(c, "TRANCHE_REPAID", amount, actor);
}

export function repayLoan(c, kind, amount, actor) {
  if (c.status === "CLOSED") throw new ContractError(409, "Договор закрыт");
  if (kind === "FULL") {
    const sum = c.outstanding;
    c.outstanding = 0;
    c.status = "CLOSED";
    op(c, "FULL_REPAYMENT", sum, actor);
    return;
  }
  if (amount <= 0) throw new ContractError(422, "Укажите сумму больше нуля");
  if (amount > c.outstanding) throw new ContractError(422, "Сумма больше остатка долга");
  if (amount === c.outstanding) return repayLoan(c, "FULL", 0, actor);
  const oldPayment = c.payment;
  c.outstanding -= amount;
  op(c, "PARTIAL_REPAYMENT", amount, actor);
  const remaining = paymentDates(c).filter((d) => d > c.contract_date).length;
  const newPayment = annuity(c.outstanding, c.rate, remaining);
  addVersion(c, { changes: [{ field: "payment", old: oldPayment, new: newPayment }], author: actor, reason: "частичное досрочное погашение" });
  c.payment = newPayment;
}

export function changeTerms(c, { rate, term_months }, author, reason) {
  if (c.status === "CLOSED") throw new ContractError(409, "Договор закрыт");
  const changes = [];
  if (rate !== undefined && Number(rate) !== c.rate) changes.push({ field: "rate", old: c.rate, new: Number(rate) });
  if (term_months !== undefined && Number(term_months) !== c.term_months) changes.push({ field: "term_months", old: c.term_months, new: Number(term_months) });
  if (!changes.length) throw new ContractError(422, "Условия не изменились");
  addVersion(c, { changes, author, reason });
  for (const ch of changes) c[ch.field] = ch.new;
  if (!isLine(c)) {
    const remaining = paymentDates(c).filter((d) => d > c.contract_date).length;
    c.payment = annuity(c.outstanding, c.rate, remaining);
  }
}

export function contractView(c) {
  const schedule = computeSchedule(c);
  const next = schedule[0];
  return {
    id: c.id,
    number: c.number,
    application_id: c.application_id,
    company_name: c.company_name,
    inn: c.inn,
    product_code: c.product_code,
    variant: c.variant,
    status: c.status,
    rate: c.rate,
    term_months: c.term_months,
    start_date: c.start_date,
    contract_date: c.contract_date,
    limit: c.limit,
    amount: c.amount,
    available: isLine(c) ? lineAvailable(c) : null,
    outstanding: c.outstanding,
    next_payment: next ? { date: next.due_date, amount: next.payment } : null,
    tranche_min: c.tranche_min,
    tranche_window_end: c.tranche_window_end,
    tranches: c.tranches.map((t) => ({ ...t })),
    current_version: c.versions.length,
  };
}

export function scheduleForVersion(c, version) {
  const v = Number(version) || c.versions.length;
  if (v >= c.versions.length) return computeSchedule(c);
  return c.versions[v - 1].schedule || [];
}
