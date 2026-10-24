import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, ButtonLink, Card, Disclosure, Field, FileUpload, FormError, History, KeyValue, Money, Note, StatementRow, StatusLine } from "../components/index.js";
import { api, fieldErrors } from "../api/index.js";
import { useToast } from "../layout/Shell.jsx";
import { useIsMobile, useResource } from "../lib/hooks.js";
import { formatDayMonth, formatDateTime, formatMoney, formatRate, formatTime } from "../lib/format.js";
import { annuityPayment } from "../lib/payment.js";
import { HISTORY_NAMES, productLine, productName, variantName } from "../lib/labels.js";
import { LoadError, PageSkeleton } from "./StatePages.jsx";

export function historyItems(list = []) {
  return list.map((h) => ({
    time: formatDateTime(h.at),
    event: `${HISTORY_NAMES[h.from_status] || h.from_status} → ${HISTORY_NAMES[h.to_status] || h.to_status}`,
    actor: [h.actor?.name || "Система", h.reason].filter(Boolean).join(" · "),
  }));
}

const fromAnalyst = (app) => app.status === "NEEDS_INFO" && app.info_request?.source === "ANALYST";

function caption(app, live) {
  if (live?.step_label || app.step_label) return live?.step_label || app.step_label;
  const until = app.decision?.expires_at ? ` Действует до ${formatDayMonth(app.decision.expires_at)}` : "";
  switch (app.status) {
    case "APPROVED": return "Банк одобрил заявку." + until;
    case "COUNTER_OFFER": return "Банк предлагает другие условия." + until;
    case "MANUAL_REVIEW": return "Обычно в течение дня";
    case "NEEDS_INFO": return fromAnalyst(app) ? "Специалисту не хватает данных" : "Банку не хватает данных";
    default: return undefined;
  }
}

const offerLine = (o, app) => [o.product_code !== app.product_code && productName(o.product_code), variantName(o.variant), formatRate(o.rate), `${o.term_months} мес`].filter(Boolean).join(" · ");

/** «Отказаться» без модалки: подтверждение прямо на месте. */
function CancelLink({ label, onCancel }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!ask) return <Button variant="link" onClick={() => setAsk(true)}>{label}</Button>;
  return (
    <span className="row row-4 row--baseline">
      <span>Отказаться? Вернуть заявку будет нельзя</span>
      <Button variant="link" loading={busy} onClick={async () => { setBusy(true); try { await onCancel(); } finally { setBusy(false); } }}>Да, отказаться</Button>
      <Button variant="link" onClick={() => setAsk(false)}>Нет</Button>
    </span>
  );
}

function CounterOffer({ app, mobile, refresh }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const offers = app.decision?.offers || [];
  const can = (a) => app.allowed_actions?.includes(a);
  const accept = async (o) => {
    setBusy(o.id);
    setError(null);
    try { await api.accept(app.id, o.id); await refresh(); } catch (err) { setError(err.message); } finally { setBusy(null); }
  };
  return (
    <section className="stack stack-6">
      <h2 className="display">Банк готов дать {formatMoney(offers[0]?.amount)}</h2>
      <div className="grid--offers">
        {offers.map((o, i) => (
          <Card key={o.id}>
            <div className="card-fill">
              <div className="stack stack-1">
                <Money value={o.amount} size="money" />
                <p>{offerLine(o, app)}</p>
              </div>
              {o.variant === "SECURED" && app.variant !== "SECURED" && <p className="small muted">Нужно будет описать залог, заявку посмотрит специалист</p>}
              {can("accept") && (
                <div className="push-down">
                  <Button variant={i === 0 ? "primary" : "secondary"} block={mobile} loading={busy === o.id} onClick={() => accept(o)}>
                    {o.variant === "SECURED" && app.variant !== "SECURED" ? "Оформить с залогом" : `Взять ${formatMoney(o.amount)}`}
                  </Button>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
      <div className={mobile ? "stack stack-2 start" : "row row-6 row--baseline"}>
        {app.decision?.expires_at && <p>Предложение действует до {formatDayMonth(app.decision.expires_at)}</p>}
        {can("cancel") && <CancelLink label="Отказаться" onCancel={async () => { await api.cancel(app.id); await refresh(); }} />}
      </div>
      <FormError>{error}</FormError>
    </section>
  );
}

function Approved({ app, refresh }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [expired, setExpired] = useState(false);
  const o = app.decision?.offers?.[0] || { amount: app.amount, rate: app.decision?.rate, term_months: app.term_months };
  const line = app.product_code === "CREDIT_LINE";
  const can = (a) => app.allowed_actions?.includes(a);
  const accept = async () => {
    setBusy(true);
    setError(null);
    try { await api.accept(app.id, o.id); await refresh(); } catch (err) {
      if (err.status === 410) setExpired(true);
      else setError(err.message);
    } finally { setBusy(false); }
  };
  return (
    <section className="stack stack-6">
      <h2 className="display">{line ? "Кредитная линия одобрена" : "Кредит одобрен"}</h2>
      <Card>
        <div className="stack stack-6">
          <div className="stack stack-1">
            <Money value={o.amount} size="money" />
            <p>{formatRate(o.rate)} · {o.term_months} мес{line ? "" : ` · платёж ${formatMoney(annuityPayment(o.amount, o.rate, o.term_months))} в месяц`}</p>
            {app.decision?.expires_at && <p className="muted">Действует до {formatDayMonth(app.decision.expires_at)}</p>}
          </div>
          <div className="row row-6">
            {can("accept") && <Button disabled={expired} loading={busy} onClick={accept}>Перейти к подписанию</Button>}
            {can("cancel") && <CancelLink label="Отказаться от кредита" onCancel={async () => { await api.cancel(app.id); await refresh(); }} />}
          </div>
          {expired && <FormError>Срок предложения истёк {formatDayMonth(app.decision?.expires_at)}. Подайте новую заявку</FormError>}
          <FormError>{error}</FormError>
        </div>
      </Card>
    </section>
  );
}

function NeedsInfo({ app, refresh }) {
  const items = app.info_request?.items || [];
  const wantsStatement = items.some((i) => i.code === "STATEMENT");
  const wantsCollateral = items.some((i) => i.code === "COLLATERAL");
  const [statements, setStatements] = useState(app.statements || []);
  const [collateral, setCollateral] = useState({ type: app.collateral?.type || "", description: app.collateral?.description || "", estimated_value: String(app.collateral?.estimated_value || "") });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const types = useResource(() => (wantsCollateral ? api.collateralTypes() : []), [wantsCollateral]);
  const minValue = Math.ceil(app.amount / 2);

  const upload = async (file) => {
    setUploading(true);
    setUploadError(null);
    try { const s = await api.uploadStatement(app.id, file); setStatements((l) => [...l, s]); } catch (err) { setUploadError(err.message); } finally { setUploading(false); }
  };
  const send = async () => {
    setBusy(true);
    setErrors({});
    try {
      if (wantsCollateral) await api.updateApplication(app.id, { collateral: { ...collateral, estimated_value: Number(collateral.estimated_value) || 0 } });
      await api.submitApplication(app.id);
      await refresh();
    } catch (err) {
      const fe = fieldErrors(err);
      setErrors(Object.keys(fe).length ? fe : { form: err.message });
    } finally { setBusy(false); }
  };

  return (
    <section className="stack stack-6">
      <h2 className="display">{app.info_request?.title || "Банку не хватает данных"}</h2>
      <Card>
        <div className="stack stack-6">
          {items.map((i, k) => <Note key={k}><p>{i.message}</p></Note>)}
          {wantsStatement && (
            <>
              <FileUpload hint="" checking={uploading} results={uploadError ? [{ type: "fail", text: uploadError }] : []} onSelect={upload} />
              {statements.length > 0 && <div>{statements.map((s) => <StatementRow key={s.id} statement={s} onDelete={async () => { await api.deleteStatement(app.id, s.id); setStatements((l) => l.filter((x) => x.id !== s.id)); }} />)}</div>}
              <FormError>{errors.statements}</FormError>
            </>
          )}
          {wantsCollateral && (
            <div className="stack stack-4" style={{ maxWidth: 480 }}>
              {!app.collateral?.type && (
                <Field label="Тип залога" variant="select" options={(types.data || []).map((c) => ({ value: c.code, label: c.label }))} value={collateral.type} onChange={(v) => setCollateral({ ...collateral, type: v })} error={errors["collateral.type"]} />
              )}
              <Field label="Описание залога" value={collateral.description} onChange={(v) => setCollateral({ ...collateral, description: v })} error={errors["collateral.description"]} />
              <Field label="Оценочная стоимость" variant="money" value={collateral.estimated_value} onChange={(v) => setCollateral({ ...collateral, estimated_value: v })} hint={`Не меньше ${formatMoney(minValue)} — половины суммы`} error={errors["collateral.estimated_value"]} />
            </div>
          )}
          <div className="stack stack-4 start">
            <Button loading={busy} onClick={send}>Отправить данные</Button>
            <FormError>{errors.form}</FormError>
          </div>
        </div>
      </Card>
    </section>
  );
}

function CodeInput({ value, onChange, error }) {
  const refs = useRef([]);
  const digits = Array.from({ length: 6 }, (_, i) => value[i] || "");
  const put = (i, ch) => {
    const next = digits.slice();
    next[i] = ch;
    onChange(next.join("").slice(0, 6));
  };
  return (
    <div className={"code-cells" + (error ? " is-error" : "")} role="group" aria-label="Код">
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => (refs.current[i] = el)}
          className="code-cell"
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          aria-label={`Цифра ${i + 1}`}
          aria-invalid={error ? true : undefined}
          value={d}
          onChange={(e) => {
            const v = e.target.value.replace(/\D/g, "");
            if (v.length > 1) {
              onChange(v.slice(0, 6));
              refs.current[Math.min(v.length, 5)]?.focus();
              return;
            }
            put(i, v);
            if (v && i < 5) refs.current[i + 1]?.focus();
          }}
          onKeyDown={(e) => { if (e.key === "Backspace" && !d && i > 0) refs.current[i - 1]?.focus(); }}
        />
      ))}
    </div>
  );
}

function Signing({ app, refresh }) {
  const toast = useToast();
  const [code, setCode] = useState(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const asked = useRef(false);
  const line = app.product_code === "CREDIT_LINE";
  const rate = app.decision?.rate;

  const getCode = async () => {
    setError(null);
    setValue("");
    try { setCode(await api.signingCode(app.id)); } catch (err) { setError(err.message); }
  };
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    getCode();
  }, []);

  const sign = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.signingConfirm(app.id, value);
      toast("Договор подписан");
      await refresh();
    } catch (err) {
      setError(err.status === 400 ? "Код неверный или устарел" : err.message);
    } finally { setBusy(false); }
  };

  const terms = [
    ["Продукт", `${productName(app.product_code)}, ${variantName(app.variant)}`],
    [line ? "Лимит" : "Сумма", formatMoney(app.amount)],
    ["Ставка", `${formatRate(rate)} годовых`],
    ["Срок", `${app.term_months} мес`],
    !line && ["Платёж в месяц", formatMoney(annuityPayment(app.amount, rate, app.term_months))],
    app.owner_guarantee_consent && ["Поручитель", "Собственник компании"],
    app.collateral && ["Залог", `${app.collateral.description}, ${formatMoney(app.collateral.estimated_value)}`],
  ].filter(Boolean).map(([label, value]) => ({ label, value }));

  return (
    <div className="grid grid--halves">
      <Card>
        <div className="stack stack-2">
          <h2 className="h2">Итоговые условия</h2>
          <KeyValue items={terms} />
        </div>
      </Card>
      <Card>
        <div className="stack stack-6">
          <div className="stack stack-1">
            <p className="h2 num">{code ? `Код для подписи: ${code.code.replace(/(\d{3})(\d{3})/, "$1 $2")} · действует до ${formatTime(code.expires_at)}` : "Получаем код для подписи"}</p>
            <p className="muted measure">В реальном банке код придёт в приложение. Для демо он показан здесь</p>
          </div>
          <div className="stack stack-2">
            <span>Код</span>
            <CodeInput value={value} onChange={(v) => { setValue(v); setError(null); }} error={!!error} />
            {error && (
              <div className="row row-4 row--baseline">
                <FormError>{error}</FormError>
                <Button variant="link" onClick={getCode}>Получить новый код</Button>
              </div>
            )}
          </div>
          <div><Button loading={busy} disabled={value.length < 6} onClick={sign}>Подписать кодом</Button></div>
        </div>
      </Card>
    </div>
  );
}

export function ApplicationPage() {
  const { id } = useParams();
  const mobile = useIsMobile();
  const res = useResource(() => Promise.all([api.application(id), api.history(id)]), [id]);
  const [live, setLive] = useState(null);

  useEffect(() => {
    setLive(null);
    return api.subscribeStatus(id, (msg) => {
      setLive(msg);
      res.reload({ silent: true });
    });
  }, [id]);

  if (res.loading && !res.data) return <PageSkeleton />;
  if (res.error && !res.data) return <LoadError error={res.error} />;
  const [app, history] = res.data;
  const refresh = () => res.reload({ silent: true });
  const status = app.status;
  const reasons = app.decision?.reasons?.length ? app.decision.reasons : null;
  const showWhy = reasons && ["COUNTER_OFFER", "MANUAL_REVIEW", "REJECTED", "APPROVED"].includes(status);

  const head = (
    <div className="stack stack-1">
      <h1 className="h1">Заявка №{app.id}</h1>
      <p className="muted">{productLine(app, { term: status !== "REJECTED" })}</p>
    </div>
  );
  const line = status !== "DRAFT" && status !== "CANCELLED" && (
    <Card>
      <StatusLine status={status} caption={["SIGNING", "ISSUED"].includes(status) ? null : caption(app, live)} orientation={mobile ? "vertical" : "horizontal"} at={fromAnalyst(app) ? 2 : undefined} />
    </Card>
  );

  if (status === "SIGNING") return <main className="page">{head}{line}<Signing app={app} refresh={refresh} /></main>;
  if (status === "ISSUED") {
    return (
      <main className="page">
        {head}
        {line}
        <div className="stack stack-6 start">
          <h2 className="display">{app.product_code === "CREDIT_LINE" ? "Лимит открыт" : "Деньги зачислены на счёт"}</h2>
          {app.contract_id && <ButtonLink variant="primary" to={`/contracts/${app.contract_id}`}>Открыть договор</ButtonLink>}
        </div>
      </main>
    );
  }

  const main = (
    <div className="stack stack-8">
      {status === "COUNTER_OFFER" && <CounterOffer app={app} mobile={mobile} refresh={refresh} />}
      {status === "NEEDS_INFO" && <NeedsInfo key={app.updated_at} app={app} refresh={refresh} />}
      {status === "APPROVED" && <Approved app={app} refresh={refresh} />}
      {status === "MANUAL_REVIEW" && <h2 className="display">Заявку смотрит специалист</h2>}
      {status === "REJECTED" && <h2 className="display">Сейчас мы не можем выдать кредит</h2>}
      {status === "CANCELLED" && <h2 className="display">Заявка отменена</h2>}
      {status === "DRAFT" && (
        <div className="stack stack-6 start">
          <h2 className="display">Заявка ещё не отправлена</h2>
          <ButtonLink variant="primary" to={`/apply?id=${app.id}`}>Продолжить анкету</ButtonLink>
        </div>
      )}
      {showWhy && (
        <Card>
          <div className="stack stack-2">
            <h2 className="h2">Почему так решили</h2>
            <ul className="list-plain list-lined">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
        </Card>
      )}
    </div>
  );

  const historyCard = (
    <Card>
      {mobile ? (
        <Disclosure title="История" plain><History items={historyItems(history)} /></Disclosure>
      ) : (
        <div className="stack stack-2">
          <h2 className="h2">История</h2>
          <History items={historyItems(history)} />
        </div>
      )}
    </Card>
  );

  return (
    <main className="page">
      {head}
      {line}
      <div className="grid grid--aside">
        {main}
        {historyCard}
      </div>
    </main>
  );
}
