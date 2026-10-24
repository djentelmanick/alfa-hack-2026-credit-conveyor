import { useState } from "react";
import { useParams } from "react-router-dom";
import { Button, Card, DataTable, Field, FormError, StatusBadge } from "../components/index.js";
import { api, fieldErrors } from "../api/index.js";
import { useResource } from "../lib/hooks.js";
import { formatDate, formatMoney, formatRate, parseDateNumeric } from "../lib/format.js";
import { CHANGE_FIELDS, OPERATION_NAMES, productName, variantName } from "../lib/labels.js";
import { ContractBadge } from "./ContractsPage.jsx";
import { LoadError, PageSkeleton } from "./StatePages.jsx";

const fmtChange = (c) => {
  const v = (x) => (c.field === "rate" ? formatRate(x) : c.field === "term_months" ? `${x} мес` : formatMoney(x));
  return `${CHANGE_FIELDS[c.field] || c.field}: ${v(c.old)} → ${v(c.new)}`;
};

const schCols = [
  { key: "n", title: "№", width: 40 },
  { key: "due", title: "Дата", render: (r) => formatDate(r.due_date) },
  { key: "payment", title: "Платёж", numeric: true, render: (r) => formatMoney(r.payment) },
  { key: "principal", title: "Основной долг", numeric: true, render: (r) => formatMoney(r.principal) },
  { key: "interest", title: "Проценты", numeric: true, render: (r) => formatMoney(r.interest) },
  { key: "balance", title: "Остаток после", numeric: true, render: (r) => formatMoney(r.balance) },
];
const opCols = [
  { key: "date", title: "Дата договора", render: (r) => formatDate(r.contract_date) },
  { key: "op", title: "Операция", render: (r) => OPERATION_NAMES[r.type] || r.label || r.type },
  { key: "amount", title: "Сумма", numeric: true, render: (r) => formatMoney(r.amount) },
  { key: "actor", title: "Кто" },
  { key: "balance", title: "Остаток после", numeric: true, render: (r) => formatMoney(r.balance_after) },
];

/** Действие с полем суммы: показывает ошибку 422 под полем. */
function useAction(after) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try { const r = await fn(); await after(); return r; } catch (err) { setError(err.message); return null; } finally { setBusy(false); }
  };
  return { busy, error, run, setError };
}

function Tranches({ c, canAct, reload }) {
  const [amount, setAmount] = useState("");
  const [selected, setSelected] = useState(null);
  const [repay, setRepay] = useState("");
  const take = useAction(reload);
  const back = useAction(reload);
  const open = c.tranches.filter((t) => t.outstanding > 0);
  const tr = c.tranches.find((t) => t.id === selected) || open[0];
  const cols = [
    { key: "amount", title: "Сумма", numeric: true, render: (t) => formatMoney(t.amount) },
    { key: "issued", title: "Выдан", render: (t) => formatDate(t.issued_at) },
    { key: "due", title: "Вернуть до", render: (t) => formatDate(t.due_at) },
    { key: "left", title: "Остаток", numeric: true, render: (t) => formatMoney(t.outstanding) },
    ...(canAct ? [{ key: "act", title: "", render: (t) => (t.outstanding > 0 ? <Button variant="link" onClick={() => { setSelected(t.id); setRepay(String(t.outstanding)); }}>Погасить</Button> : null) }] : []),
  ];
  return (
    <Card>
      <div className="stack stack-6">
        <div className="stack stack-1">
          <h2 className="h2">Транши</h2>
          <p className="muted">Деньги частями из лимита</p>
        </div>
        {canAct && (
          <div className="inline-form">
            <div className="inline-form__field">
              <Field label="Сумма транша" variant="money" value={amount} onChange={(v) => { setAmount(v); take.setError(null); }} hint={`От ${formatMoney(c.tranche_min)} до ${formatMoney(c.available)}`} error={take.error} />
            </div>
            <div className="inline-form__btn"><Button loading={take.busy} onClick={() => take.run(async () => { await api.takeTranche(c.id, Number(amount)); setAmount(""); })}>Взять транш</Button></div>
          </div>
        )}
        {c.tranches.length ? <DataTable caption="Транши" hideCaption columns={cols} rows={c.tranches} /> : <p className="muted">Траншей пока нет</p>}
        {canAct && tr && (
          <div className="inline-form divider-top" style={{ paddingTop: 16 }}>
            <div className="inline-form__field">
              <Field label={`Погасить транш от ${formatDate(tr.issued_at)}`} variant="money" value={repay} onChange={(v) => { setRepay(v); back.setError(null); }} hint={`Долг по траншу ${formatMoney(tr.outstanding)}`} error={back.error} />
            </div>
            <div className="inline-form__btn"><Button variant="secondary" loading={back.busy} onClick={() => back.run(async () => { await api.repayTranche(c.id, tr.id, Number(repay)); setRepay(""); })}>Погасить</Button></div>
          </div>
        )}
      </div>
    </Card>
  );
}

function EarlyRepayment({ c, reload }) {
  const [amount, setAmount] = useState("");
  const [askFull, setAskFull] = useState(false);
  const act = useAction(reload);
  return (
    <Card>
      <div className="stack stack-4">
        <h2 className="h2">Погасить досрочно</h2>
        <Field label="Сумма" variant="money" value={amount} onChange={(v) => { setAmount(v); act.setError(null); }} hint={`Не больше остатка долга ${formatMoney(c.outstanding)}`} error={act.error} />
        <div className="stack stack-4 start">
          <Button variant="secondary" loading={act.busy} onClick={() => act.run(async () => { await api.repay(c.id, "PARTIAL", Number(amount)); setAmount(""); })}>Погасить частично</Button>
          {askFull ? (
            <span className="row row-4 row--baseline">
              <span>Погасить {formatMoney(c.outstanding)}?</span>
              <Button variant="link" onClick={() => act.run(() => api.repay(c.id, "FULL"))}>Да, погасить</Button>
              <Button variant="link" onClick={() => setAskFull(false)}>Нет</Button>
            </span>
          ) : (
            <Button variant="link" onClick={() => setAskFull(true)}>Погасить полностью</Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function EditContract({ c, reload }) {
  const [rate, setRate] = useState(String(c.rate).replace(".", ","));
  const [term, setTerm] = useState(String(c.term_months));
  const [comment, setComment] = useState("");
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    setErrors({});
    try {
      await api.analystUpdateContract(c.id, { rate: Number(rate.replace(",", ".")), term_months: Number(term), comment });
      setComment("");
      await reload();
    } catch (err) {
      const fe = fieldErrors(err);
      setErrors(Object.keys(fe).length ? fe : { form: err.message });
    } finally { setBusy(false); }
  };
  return (
    <Card>
      <div className="stack stack-4">
        <h2 className="h2">Изменить договор</h2>
        <div className="grid--fields2">
          <Field label="Ставка, %" value={rate} onChange={setRate} error={errors.rate} />
          <Field label="Срок" variant="term" value={term} onChange={setTerm} error={errors.term_months} />
        </div>
        <Field label="Комментарий" value={comment} onChange={setComment} hint="Обязательно. Его увидит клиент" error={errors.comment} />
        <div><Button loading={busy} disabled={c.status === "CLOSED"} onClick={save}>Сохранить новую версию</Button></div>
        <FormError>{errors.form}</FormError>
        <p className="small muted">Увеличить сумму можно только новой заявкой</p>
      </div>
    </Card>
  );
}

function Emulation({ c, onClose, reload }) {
  const [date, setDate] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const closed = c.status === "CLOSED";
  const run = async (key, fn) => {
    setBusy(key);
    setError(null);
    try { const r = await fn(); setResult(r); await reload(); } catch (err) { setError(err.message); } finally { setBusy(null); }
  };
  const rewind = () => {
    const iso = parseDateNumeric(date);
    if (!iso) { setError("Укажите дату в формате дд.мм.гггг"); return; }
    run("rewind", () => api.emulateRewind(c.id, iso));
  };
  return (
    <aside className="emulation" aria-label="Эмуляция">
      <div className="row row-4 row--between">
        <h2 className="h2">Эмуляция</h2>
        <Button variant="link" onClick={onClose}>Закрыть</Button>
      </div>
      <div><StatusBadge kind="waiting">Режим разработчика — только для демо</StatusBadge></div>
      <Button variant="secondary" block disabled={closed} loading={busy === "next"} onClick={() => run("next", () => api.emulateNextMonth(c.id))}>Следующий месяц</Button>
      <div className="emulation__section">
        <Field label="Дата" value={date} onChange={(v) => { setDate(v); setError(null); }} placeholder="дд.мм.гггг" hint="Не раньше даты договора" error={error} />
        <Button variant="secondary" block disabled={closed} loading={busy === "rewind"} onClick={rewind}>Перемотать до даты</Button>
      </div>
      <div><Button variant="link" loading={busy === "reset"} onClick={() => run("reset", async () => { await api.emulateReset(c.id); return null; })}>Сбросить демо</Button></div>
      {closed && <p className="small muted">Договор закрыт — эмуляция недоступна. Сбросьте демо, чтобы начать заново</p>}
      {result && (
        <div className="stack stack-2 divider-top" style={{ paddingTop: 24 }}>
          <span className="body-strong">Что произошло</span>
          <ul className="list-plain list-lined">
            <li>Дата договора: {formatDate(result.contract_date)}</li>
            {result.operations.length ? result.operations.map((o, i) => <li key={i}>{OPERATION_NAMES[o.type] || o.type} — {formatMoney(o.amount)}</li>) : <li>Плановых операций не было</li>}
          </ul>
        </div>
      )}
    </aside>
  );
}

export function ContractPage({ analyst = false }) {
  const { id } = useParams();
  const [version, setVersion] = useState(null);
  const [emulation, setEmulation] = useState(false);
  const [more, setMore] = useState(false);
  const res = useResource(() => Promise.all([api.contract(id), api.operations(id), api.versions(id)]), [id]);
  const sch = useResource(() => api.schedule(id, version || undefined), [id, version]);

  if (res.loading && !res.data) return <PageSkeleton />;
  if (res.error && !res.data) return <LoadError error={res.error} home={analyst ? "/analyst" : "/"} />;
  const [c, ops, versions] = res.data;
  const reload = async () => { await res.reload({ silent: true }); await sch.reload({ silent: true }); };
  const line = c.product_code === "CREDIT_LINE";
  const canAct = !analyst && c.status !== "CLOSED";

  const summary = line
    ? [["Лимит", formatMoney(c.limit)], ["Доступно", formatMoney(c.available)], ["Долг", formatMoney(c.outstanding)], ["Следующий платёж", c.next_payment ? formatMoney(c.next_payment.amount) : "—", c.next_payment && formatDate(c.next_payment.date)]]
    : [["Сумма кредита", formatMoney(c.amount)], ["Остаток долга", formatMoney(c.outstanding)], ["Следующий платёж", c.next_payment ? formatMoney(c.next_payment.amount) : "—", c.next_payment && formatDate(c.next_payment.date)], ["Ставка и срок", `${formatRate(c.rate)} · ${c.term_months} мес`]];

  const current = c.current_version;
  const versionOptions = versions.map((v) => ({ value: String(v.version_no), label: `Версия ${v.version_no}${v.version_no === current ? " — действует" : ""}` }));
  const rows = sch.data || [];
  const visible = !line && !more ? rows.slice(0, 4) : rows;

  return (
    <div className="contract-wrap">
      <main className="page">
        <div className="row row-4 row--between row--start">
          <div className="stack stack-1">
            <div className="row row-4">
              <h1 className="h1">Договор №{c.number}</h1>
              <ContractBadge status={c.status} />
            </div>
            <p className="muted">{productName(c.product_code)} · {variantName(c.variant)} · {c.company_name}</p>
            <p className="body-strong">Дата договора: {formatDate(c.contract_date)}</p>
          </div>
          <Button variant="secondary" aria-expanded={emulation} onClick={() => setEmulation((v) => !v)}>Эмуляция</Button>
        </div>

        <Card>
          <dl className="summary-dl">
            {summary.map(([label, value, note]) => (
              <div key={label} className="stack stack-1">
                <dt>{label}</dt>
                <dd className="value">{value}</dd>
                {note && <dd className="note-sm">{note}</dd>}
              </div>
            ))}
          </dl>
        </Card>

        <div className="grid grid--aside">
          <div className="stack stack-8">
            {line && <Tranches c={c} canAct={canAct} reload={reload} />}
            <Card>
              <div className="stack stack-4">
                <div className="row row-4 row--between" style={{ alignItems: "flex-end" }}>
                  <h2 className="h2" style={{ paddingBottom: 12 }}>График платежей</h2>
                  <div style={{ width: 280, maxWidth: "100%" }}>
                    <Field label="Версия договора" variant="select" placeholder={null} options={versionOptions} value={String(version || current)} onChange={(v) => setVersion(Number(v) === current ? null : Number(v))} />
                  </div>
                </div>
                {rows.length ? <DataTable caption="График платежей" hideCaption columns={schCols} rows={visible} rowKey="n" /> : <p className="muted">Платежей по графику нет</p>}
                {!line && rows.length > 4 && (
                  <div><Button variant="link" onClick={() => setMore(!more)}>{more ? "Скрыть платежи" : `Показать ещё ${rows.length - 4} платежей`}</Button></div>
                )}
              </div>
            </Card>
            <Card>
              <div className="stack stack-4">
                <h2 className="h2">Журнал операций</h2>
                <DataTable caption="Журнал операций" hideCaption columns={opCols} rows={ops} />
              </div>
            </Card>
          </div>
          <div className="stack stack-8">
            {!line && canAct && <EarlyRepayment c={c} reload={reload} />}
            {analyst && <EditContract key={c.current_version} c={c} reload={reload} />}
            <Card>
              <div className="stack stack-2">
                <h2 className="h2">Версии договора</h2>
                <ul className="list-plain">
                  {versions.map((v) => (
                    <li key={v.version_no} className="stack stack-1" style={{ padding: "16px 0", borderBottom: "1px solid var(--line)" }}>
                      <span className="body-strong">Версия {v.version_no}</span>
                      <span>{v.changes?.length ? v.changes.map(fmtChange).join("; ") : v.reason || "Договор подписан"}</span>
                      <span className="small muted">{[v.author, v.changes?.length ? v.reason : null, formatDate(v.created_at)].filter(Boolean).join(" · ")}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </Card>
          </div>
        </div>
      </main>
      {emulation && <Emulation c={c} onClose={() => setEmulation(false)} reload={reload} />}
    </div>
  );
}
