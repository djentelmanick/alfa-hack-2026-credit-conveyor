import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, Card, DataTable, Disclosure, Field, FormError, History, Icon, KeyValue, StatusBadge } from "../../components/index.js";
import { api, fieldErrors } from "../../api/index.js";
import { useToast } from "../../layout/Shell.jsx";
import { useResource } from "../../lib/hooks.js";
import { formatDateNumeric, formatMoney, formatRate } from "../../lib/format.js";
import { CHECK_OUTCOMES, CONSENT_LABELS, OUTCOME_NAMES, REJECT_REASONS as REJECT_REASON_LABELS, RISK_LABELS, SOURCE_NAMES, productName, reasonLabel, shortId, variantName } from "../../lib/labels.js";
import { historyItems } from "../ApplicationPage.jsx";
import { LoadError, PageSkeleton } from "../StatePages.jsx";

const ACTIONS = [
  ["approve", "Одобрить"],
  ["counter", "Контрпредложение"],
  ["info", "Запросить информацию"],
  ["reject", "Отказать"],
  ["recheck", "Повторить проверки"],
];
const INFO_OPTIONS = [{ value: "STATEMENT", label: "Выписка по счёту" }, { value: "COLLATERAL", label: "Данные залога" }];
const REJECT_REASONS = Object.entries(REJECT_REASON_LABELS).map(([value, label]) => ({ value, label }));
const PRODUCT_OPTIONS = [{ value: "LUMP_SUM", label: "Кредит одной суммой" }, { value: "REVOLVING_LINE", label: "Кредитная линия" }];
const VARIANT_OPTIONS = [{ value: "SECURED", label: "С залогом" }, { value: "UNSECURED", label: "Без залога" }];

const sourceName = (s) => SOURCE_NAMES[s.source] || s.source;
const sourceSubject = (s, inn) => {
  const subject = s.data?.subject_inn;
  if (!subject) return null;
  return subject === inn ? "компания" : `ИНН ${subject}`;
};
const signed = (n) => (n > 0 ? `+${n}` : String(n).replace("-", "−"));

const cmpCols = [
  { key: "title", title: "Проверка" },
  { key: "expected", title: "Ожидали" },
  { key: "actual", title: "Получили", numeric: true },
  {
    key: "res",
    title: "Результат",
    render: (r) => {
      const ok = r.outcome === "PASSED";
      return <span className="ok-line"><Icon name={ok ? "circle-check" : "circle-alert"} style={{ color: ok ? "var(--success)" : r.outcome === "NO_DATA" ? "var(--attention)" : "var(--danger)" }} />{CHECK_OUTCOMES[r.outcome] || r.outcome}</span>;
    },
  },
];
const turnoverCols = [
  { key: "month", title: "Месяц" },
  { key: "in", title: "Поступления", numeric: true, render: (r) => formatMoney(r.incoming) },
  { key: "out", title: "Списания", numeric: true, render: (r) => formatMoney(r.outgoing) },
];
const payerCols = [
  { key: "name", title: "Плательщик", render: (r) => (r.name ? <span className="two-line"><span>{r.name}</span><span>ИНН {r.inn}</span></span> : `ИНН ${r.inn}`) },
  { key: "share", title: "Доля в поступлениях", numeric: true, render: (r) => `${String(r.share_pct).replace(".", ",")}%` },
];

function Radio({ state }) {
  return <span className={"radio-dot" + (state === "on" ? " is-on" : state === "off" ? " is-off" : "")} />;
}

function DecisionPanel({ report, reload }) {
  const toast = useToast();
  const app = report.application;
  const failed = [...new Set(report.sources.filter((s) => s.status !== "OK").map(sourceName))];
  const canApprove = !report.sources.some((s) => s.status === "FAILED");
  const list = ACTIONS.filter(([id]) => id !== "recheck" || failed.length);
  const products = useResource(() => api.products(), []);
  const variant = products.data?.find((p) => p.code === app.product_code)?.variants.find((v) => v.variant === app.variant);
  const systemRate = report.decision?.system_rate || variant?.rate_min || "";
  const [action, setAction] = useState(canApprove ? "approve" : "recheck");
  const [rate, setRate] = useState(String(systemRate).replace(".", ","));
  const [options, setOptions] = useState([{ product_code: app.product_code, variant: app.variant, amount: String(app.amount), term_months: String(app.term_months), rate: String(systemRate).replace(".", ",") }]);
  const [items, setItems] = useState([{ code: "", message: "" }]);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!systemRate) return;
    const v = String(systemRate).replace(".", ",");
    setRate((x) => x || v);
    setOptions((list) => list.map((o) => (o.rate ? o : { ...o, rate: v })));
  }, [systemRate]);

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
      if (err.code === "SOURCES_NOT_READY") setErrors({ form: "Одобрить нельзя: ответили не все источники. Повторите проверки" });
      else setErrors(Object.keys(fe).length ? fe : { form: err.message });
    } finally { setBusy(false); }
  };

  return (
    <Card>
      <div className="stack stack-4">
        <div className="stack stack-1">
          <span className="small muted">Рекомендация системы</span>
          <span className="h2">{OUTCOME_NAMES[report.decision?.outcome] || "Решение за специалистом"}</span>
          {report.decision?.manual_reasons?.length > 0 && <span className="small muted">{report.decision.manual_reasons.map(reasonLabel).join(" · ")}</span>}
        </div>
        <div role="radiogroup" aria-label="Решение" className="stack divider-top">
          {list.map(([id, label]) => {
            const disabled = id === "approve" && !canApprove;
            const selected = !disabled && action === id;
            return (
              <div key={id} className="decision-item">
                <button type="button" role="radio" aria-checked={selected} disabled={disabled} className="decision-radio" onClick={() => setAction(id)}>
                  <Radio state={selected ? "on" : disabled ? "off" : "idle"} />
                  <span>{label}</span>
                </button>
                {disabled && <p className="small muted" style={{ marginLeft: 28 }}>Одобрить можно, когда ответят все источники. Повторите проверки</p>}
                {selected && id === "approve" && (
                  <div className="decision-item__body">
                    <Field label="Ставка, %" value={rate} onChange={setRate} hint={variant ? `От ${formatRate(variant.rate_min)} до ${formatRate(variant.rate_max)}` : undefined} error={errors.rate} />
                  </div>
                )}
                {selected && id === "counter" && (
                  <div className="decision-item__body">
                    {options.map((o, i) => (
                      <div key={i} className="stack stack-4">
                        <span className="body-strong">Вариант {i + 1}</span>
                        <Field label="Продукт" variant="select" placeholder={null} options={PRODUCT_OPTIONS} value={o.product_code} onChange={(v) => setOption(i, { product_code: v })} />
                        <Field label="Вариант" variant="select" placeholder={null} options={VARIANT_OPTIONS} value={o.variant} onChange={(v) => setOption(i, { variant: v })} />
                        <Field label="Сумма" variant="money" value={o.amount} onChange={(v) => setOption(i, { amount: v })} error={errors[`options[${i}].amount`]} />
                        <div className="grid--fields2">
                          <Field label="Срок" variant="term" value={o.term_months} onChange={(v) => setOption(i, { term_months: v })} error={errors[`options[${i}].term_months`]} />
                          <Field label="Ставка, %" value={o.rate} onChange={(v) => setOption(i, { rate: v })} error={errors[`options[${i}].rate`]} />
                        </div>
                      </div>
                    ))}
                    <FormError>{errors.options}</FormError>
                    {options.length < 5 && <div><Button variant="link" onClick={() => setOptions([...options, { ...options[options.length - 1] }])}>Добавить вариант</Button></div>}
                  </div>
                )}
                {selected && id === "info" && (
                  <div className="decision-item__body">
                    {items.map((it, i) => (
                      <div key={i} className="stack stack-4">
                        <Field label="Что запросить" variant="select" placeholder="Выберите пункт" options={INFO_OPTIONS} value={it.code} onChange={(v) => setItem(i, { code: v })} />
                        <Field label="Текст клиенту" value={it.message} onChange={(v) => setItem(i, { message: v })} error={errors[`items[${i}].message`]} />
                      </div>
                    ))}
                    <FormError>{errors.items}</FormError>
                    {items.length < INFO_OPTIONS.length && <div><Button variant="link" onClick={() => setItems([...items, { code: "", message: "" }])}>Добавить пункт</Button></div>}
                  </div>
                )}
                {selected && id === "reject" && (
                  <div className="decision-item__body">
                    <Field label="Причина" variant="select" placeholder="Выберите причину" options={REJECT_REASONS} value={reason} onChange={setReason} error={errors.reason_code} />
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

function decisionSummary(d) {
  if (!d) return null;
  const parts = [`Система: ${(OUTCOME_NAMES[d.outcome] || d.outcome).toLowerCase()}${d.system_rate ? `, ставка ${formatRate(d.system_rate)}` : ""}${d.rule_code ? ` — ${reasonLabel(d.rule_code)}` : ""}`];
  if (d.manual_reasons?.length) parts.push(`На ручную проверку: ${d.manual_reasons.map(reasonLabel).join(", ")}`);
  if (d.analyst) parts.push(`${d.analyst.name}, ${formatDateNumeric(d.analyst.decided_at)}${d.analyst.rate ? `, ставка ${formatRate(d.analyst.rate)}` : ""}: ${d.analyst.comment}`);
  return parts;
}

export function ReportPage() {
  const { id } = useParams();
  const res = useResource(() => api.analystReport(id), [id]);
  if (res.loading && !res.data) return <PageSkeleton />;
  if (res.error && !res.data) return <LoadError error={res.error} home="/analyst" />;
  const r = res.data;
  const app = r.application;
  const p = r.provided || {};

  const provided = [
    { label: "Анкета", value: `${productName(app.product_code)}, ${variantName(app.variant)} · ${formatMoney(app.amount)} на ${app.term_months} мес` },
    { label: "ФИО и паспорт", value: `${p.applicant?.full_name}${p.applicant?.passport_verified ? " · паспорт проверен" : " · паспорт не проверен"}` },
    p.consents?.length && { label: "Согласия", value: `${p.consents.map((c) => (CONSENT_LABELS[c.type] || c.type).replace("Согласие на ", "")).join(", ")} — ${formatDateNumeric(p.consents[p.consents.length - 1].given_at)}` },
    p.collateral && { label: "Залог", value: `${p.collateral.description}, ${formatMoney(p.collateral.estimated_value)}` },
    p.owner_guarantee_consent && { label: "Поручительство", value: "Собственник согласен стать поручителем" },
    p.statements?.length && { label: "Выписки", value: p.statements.map((s) => `${s.bank_name}, ${s.months_covered} мес, выручка за год ${formatMoney(s.annual_revenue)}${s.inn_matches ? "" : " — ИНН не совпадает"}`).join("; ") },
  ].filter(Boolean);

  const scoring = r.scoring && [
    { label: "Итог", value: `${r.scoring.total} баллов` },
    { label: "Класс риска", value: RISK_LABELS[r.scoring.risk_class] || r.scoring.risk_class },
    { label: "Сколько уходит на платежи по кредитам", value: `${String(r.scoring.debt_load_pct).replace(".", ",")}% выручки` },
    ...(r.scoring.factors || []).map((f) => ({ label: `${f.label}: ${f.value}`, value: <span className="num">{signed(f.points)}</span> })),
  ];
  const summary = decisionSummary(r.decision);

  return (
    <main className="page page--report">
      <div className="stack stack-1">
        <div className="row row-4">
          <h1 className="h1">Заявка №{shortId(app.id)} · {app.company?.name}</h1>
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
                {r.sources.map((s, i) => (
                  <li key={`${s.source}-${i}`} className="source-row">
                    <span>{sourceName(s)}{sourceSubject(s, app.inn) && <span className="muted"> · {sourceSubject(s, app.inn)}</span>}</span>
                    {s.status === "OK" ? (
                      <span className="ok-line"><Icon name="circle-check" />ответил</span>
                    ) : s.status === "SKIPPED" ? (
                      <span className="ok-line muted">не запрашивали: нет ответа ЕГРЮЛ</span>
                    ) : (
                      <span className="ok-line body-strong"><Icon name="circle-alert" style={{ color: "var(--attention)" }} />нет ответа (попыток: {s.attempts})</span>
                    )}
                  </li>
                ))}
              </ul>
            </Disclosure>
            <Disclosure title="Как сравнили" defaultOpen>
              {r.comparisons.length ? <DataTable caption="Как сравнили" hideCaption columns={cmpCols} rows={r.comparisons.map((c, i) => ({ ...c, id: c.code || i }))} /> : <p className="muted">Сравнивать нечего: выписки и залога нет</p>}
            </Disclosure>
            {(r.turnover.length > 0 || r.top_payers.length > 0) && (
              <Disclosure title="Обороты и плательщики">
                <div className="stack stack-4">
                  {r.turnover.length > 0 && <DataTable caption="Обороты по месяцам" hideCaption columns={turnoverCols} rows={r.turnover.map((t) => ({ ...t, id: t.month }))} />}
                  {r.top_payers.length > 0 && <DataTable caption="Крупные плательщики" hideCaption columns={payerCols} rows={r.top_payers.map((t) => ({ ...t, id: t.inn }))} />}
                </div>
              </Disclosure>
            )}
            <Disclosure title="Скоринг" defaultOpen>
              {scoring ? <KeyValue items={scoring} /> : <p className="muted">Скоринг не считали: заявка не дошла до оценки</p>}
            </Disclosure>
            <Disclosure title="Что решили" defaultOpen>
              {summary ? <ul className="list-plain list-lined measure">{summary.map((t, i) => <li key={i}>{t}</li>)}</ul> : <p className="measure">Решения ещё нет</p>}
            </Disclosure>
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
