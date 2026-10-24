import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, Card, DataTable, Disclosure, Field, FormError, History, Icon, KeyValue, StatusBadge } from "../../components/index.js";
import { api, fieldErrors } from "../../api/index.js";
import { useToast } from "../../layout/Shell.jsx";
import { useResource } from "../../lib/hooks.js";
import { formatDateNumeric, formatMoney } from "../../lib/format.js";
import { CONSENT_LABELS, RISK_LABELS, productName, variantName } from "../../lib/labels.js";
import { historyItems } from "../ApplicationPage.jsx";
import { LoadError, PageSkeleton } from "../StatePages.jsx";

const ACTIONS = [
  ["approve", "Одобрить"],
  ["counter", "Контрпредложение"],
  ["info", "Запросить информацию"],
  ["reject", "Отказать"],
  ["recheck", "Повторить проверки"],
];
const RECOMMENDED = { APPROVE: "Одобрить", RECHECK: "Повторить проверки", REJECT: "Отказать" };
const INFO_OPTIONS = [{ value: "STATEMENT", label: "Выписка по счёту" }, { value: "COLLATERAL", label: "Данные залога" }];

const cmpCols = [
  { key: "check", title: "Проверка" },
  { key: "what", title: "Что с чем" },
  { key: "values", title: "Значения", numeric: true },
  { key: "res", title: "Результат", render: (r) => <span className="ok-line"><Icon name={r.passed ? "circle-check" : "circle-alert"} style={{ color: r.passed ? "var(--success)" : "var(--danger)" }} />{r.passed ? "Прошла" : "Не прошла"}</span> },
];

function Radio({ state }) {
  return <span className={"radio-dot" + (state === "on" ? " is-on" : state === "off" ? " is-off" : "")} />;
}

function DecisionPanel({ report, reload }) {
  const toast = useToast();
  const app = report.application;
  const failed = report.sources.filter((s) => s.status !== "OK").map((s) => s.name);
  const list = ACTIONS.filter(([id]) => id !== "recheck" || failed.length);
  const [action, setAction] = useState(report.can_approve ? "approve" : "recheck");
  const [rate, setRate] = useState("23");
  const [options, setOptions] = useState([{ product_code: app.product_code, variant: app.variant, amount: String(app.amount), term_months: String(app.term_months), rate: "23" }]);
  const [items, setItems] = useState([{ code: "", message: "" }]);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const reasons = useResource(() => api.analystRejectReasons(), []);

  if (app.status !== "MANUAL_REVIEW") {
    return (
      <Card>
        <div className="stack stack-4">
          <h2 className="h2">Решение по заявке</h2>
          <div><StatusBadge status={app.status} /></div>
          <p className="muted">Решение принимают, когда заявка на ручной проверке</p>
        </div>
      </Card>
    );
  }

  const setOption = (i, patch) => setOptions(options.map((o, k) => (k === i ? { ...o, ...patch } : o)));
  const setItem = (i, patch) => setItems(items.map((o, k) => (k === i ? { ...o, ...patch } : o)));

  const send = async () => {
    setBusy(true);
    setErrors({});
    try {
      const num = (v) => Number(String(v).replace(/\s/g, "").replace(",", "."));
      if (action === "approve") await api.analystApprove(app.id, { rate: num(rate), comment });
      if (action === "counter") await api.analystCounterOffer(app.id, { options: options.map((o) => ({ ...o, amount: num(o.amount), term_months: num(o.term_months), rate: num(o.rate) })), comment });
      if (action === "info") await api.analystRequestInfo(app.id, { items: items.filter((i) => i.code), comment });
      if (action === "reject") await api.analystReject(app.id, { reason_code: reason, comment });
      if (action === "recheck") await api.analystRecheck(app.id, { comment });
      toast("Решение отправлено клиенту");
      await reload();
    } catch (err) {
      const fe = fieldErrors(err);
      setErrors(Object.keys(fe).length ? fe : { form: err.message });
    } finally { setBusy(false); }
  };

  return (
    <Card>
      <div className="stack stack-4">
        <div className="stack stack-1">
          <span className="small muted">Рекомендация системы</span>
          <span className="h2">{RECOMMENDED[report.decision?.recommended_action] || "Одобрить"}</span>
        </div>
        <div role="radiogroup" aria-label="Решение" className="stack divider-top">
          {list.map(([id, label]) => {
            const disabled = id === "approve" && !report.can_approve;
            const selected = !disabled && action === id;
            return (
              <div key={id} className="decision-item">
                <button type="button" role="radio" aria-checked={selected} disabled={disabled} className="decision-radio" onClick={() => setAction(id)}>
                  <Radio state={selected ? "on" : disabled ? "off" : "idle"} />
                  <span>{label}</span>
                </button>
                {disabled && <p className="small muted" style={{ marginLeft: 28 }}>Одобрить можно, когда ответят все источники стоп-факторов. Повторите проверки</p>}
                {selected && id === "approve" && (
                  <div className="decision-item__body"><Field label="Ставка, %" value={rate} onChange={setRate} hint="От 23 до 29%" error={errors.rate} /></div>
                )}
                {selected && id === "counter" && (
                  <div className="decision-item__body">
                    {options.map((o, i) => (
                      <div key={i} className="stack stack-4">
                        <span className="body-strong">Вариант {i + 1}</span>
                        <Field label="Продукт" variant="select" placeholder={null} options={[{ value: "CREDIT_LINE", label: "Кредитная линия" }, { value: "LOAN", label: "Кредит одной суммой" }]} value={o.product_code} onChange={(v) => setOption(i, { product_code: v })} />
                        <Field label="Вариант" variant="select" placeholder={null} options={[{ value: "SECURED", label: "С залогом" }, { value: "UNSECURED", label: "Без залога" }]} value={o.variant} onChange={(v) => setOption(i, { variant: v })} />
                        <Field label="Сумма" variant="money" value={o.amount} onChange={(v) => setOption(i, { amount: v })} />
                        <div className="grid--fields2">
                          <Field label="Срок" variant="term" value={o.term_months} onChange={(v) => setOption(i, { term_months: v })} />
                          <Field label="Ставка, %" value={o.rate} onChange={(v) => setOption(i, { rate: v })} />
                        </div>
                      </div>
                    ))}
                    <FormError>{errors.options}</FormError>
                    <div><Button variant="link" onClick={() => setOptions([...options, { ...options[options.length - 1] }])}>Добавить вариант</Button></div>
                  </div>
                )}
                {selected && id === "info" && (
                  <div className="decision-item__body">
                    {items.map((it, i) => (
                      <div key={i} className="stack stack-4">
                        <Field label="Что запросить" variant="select" placeholder="Выберите пункт" options={INFO_OPTIONS} value={it.code} onChange={(v) => setItem(i, { code: v })} />
                        <Field label="Текст клиенту" value={it.message} onChange={(v) => setItem(i, { message: v })} />
                      </div>
                    ))}
                    <FormError>{errors.items}</FormError>
                    <div><Button variant="link" onClick={() => setItems([...items, { code: "", message: "" }])}>Добавить пункт</Button></div>
                  </div>
                )}
                {selected && id === "reject" && (
                  <div className="decision-item__body">
                    <Field label="Причина" variant="select" placeholder="Выберите причину" options={(reasons.data || []).map((r) => ({ value: r.code, label: r.label }))} value={reason} onChange={setReason} error={errors.reason_code} />
                  </div>
                )}
                {selected && id === "recheck" && <p className="small muted" style={{ marginLeft: 28 }}>Повторим запрос в источники, которые не ответили: {failed.join(", ")}</p>}
              </div>
            );
          })}
        </div>
        <Field label="Комментарий — его увидит клиент" value={comment} onChange={setComment} error={errors.comment} />
        <Button block loading={busy} onClick={send}>Отправить решение</Button>
        <FormError>{errors.form}</FormError>
      </div>
    </Card>
  );
}

export function ReportPage() {
  const { id } = useParams();
  const res = useResource(() => api.analystReport(id), [id]);
  if (res.loading && !res.data) return <PageSkeleton />;
  if (res.error && !res.data) return <LoadError error={res.error} home="/analyst" />;
  const r = res.data;
  const app = r.application;
  const p = r.provided || {};
  const q = p.questionnaire || {};

  const provided = [
    { label: "Анкета", value: `${productName(q.product_code)}, ${variantName(q.variant)} · ${formatMoney(q.amount)} на ${q.term_months} мес` },
    { label: "ФИО и паспорт", value: `${p.applicant?.full_name}${p.applicant?.passport_verified ? " · паспорт проверен" : " · паспорт не проверен"}` },
    p.consents?.length && { label: "Согласия", value: `${p.consents.map((c) => CONSENT_LABELS[c.type].replace("Согласие на ", "")).join(", ")} — ${formatDateNumeric(p.consents[p.consents.length - 1].given_at)}` },
    p.collateral && { label: "Залог", value: `${p.collateral.description}, ${formatMoney(p.collateral.estimated_value)}` },
  ].filter(Boolean);

  const scoring = r.scoring && [
    { label: "Итог", value: `${r.scoring.total} баллов из ${r.scoring.max}` },
    { label: "Класс риска", value: RISK_LABELS[r.scoring.risk_class] || r.scoring.risk_class },
    { label: "Сколько уходит на платежи по кредитам", value: `${r.scoring.debt_load_percent}% выручки` },
  ];

  return (
    <main className="page page--report">
      <div className="stack stack-1">
        <div className="row row-4">
          <h1 className="h1">Заявка №{app.id} · {app.company_name}</h1>
          <StatusBadge status={app.status} />
        </div>
        <p className="muted">ИНН {app.inn} · {productName(app.product_code)} · {variantName(app.variant)} · {formatMoney(app.amount)} на {app.term_months} мес</p>
      </div>
      <div className="grid grid--8-4">
        <Card>
          <div className="stack">
            <Disclosure title="Что предоставил клиент" defaultOpen><KeyValue items={provided} /></Disclosure>
            <Disclosure title="Что мы получили" defaultOpen>
              <ul className="list-plain">
                {r.sources.map((s) => (
                  <li key={s.name} className="source-row">
                    <span>{s.name}</span>
                    {s.status === "OK" ? (
                      <span className="ok-line"><Icon name="circle-check" />ответил</span>
                    ) : (
                      <span className="ok-line body-strong"><Icon name="circle-alert" style={{ color: "var(--attention)" }} />нет ответа ({s.attempts} попытки)</span>
                    )}
                  </li>
                ))}
              </ul>
            </Disclosure>
            <Disclosure title="Как сравнили" defaultOpen>
              {r.comparisons.length ? <DataTable caption="Как сравнили" hideCaption columns={cmpCols} rows={r.comparisons.map((c, i) => ({ ...c, id: i }))} /> : <p className="muted">Сравнивать нечего: выписки и залога нет</p>}
            </Disclosure>
            <Disclosure title="Скоринг" defaultOpen>
              {scoring ? <KeyValue items={scoring} /> : <p className="muted">Скоринг не считали: заявка не дошла до оценки</p>}
            </Disclosure>
            <Disclosure title="Что решили" defaultOpen><p className="measure">{r.decision?.summary || "Решения ещё нет"}</p></Disclosure>
            <Disclosure title="История"><History items={historyItems(r.history)} /></Disclosure>
            <Disclosure title="Договор" plain>
              {r.contract ? <Link to={`/analyst/contracts/${r.contract.id}`}>Договор №{r.contract.number}</Link> : <p className="muted">Договора пока нет — ссылка появится после выдачи</p>}
            </Disclosure>
          </div>
        </Card>
        <div className="sticky"><DecisionPanel key={app.status} report={r} reload={() => res.reload({ silent: true })} /></div>
      </div>
    </main>
  );
}
