import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, Checkbox, Field, FileUpload, FormError, Icon, Money, Note, RadioCard, RadioRow, StatementRow, StepsHeader } from "../components/index.js";
import { api, errorText, fieldErrors } from "../api/index.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useResource } from "../lib/hooks.js";
import { formatDateNumeric, formatMoney, formatRate, parseDateNumeric } from "../lib/format.js";
import { annuityPayment } from "../lib/payment.js";
import { APPLICANT_ROLES, CONSENT_LABELS, REPRESENTATIVE_CONSENT_LABELS, isRepresentative, isSoleTrader, productName, roleName, shortId, variantName } from "../lib/labels.js";
import { PageSkeleton } from "./StatePages.jsx";

// Поручительство даёт сам собственник: представитель-не-собственник только подтверждает, что согласие получено.
const guaranteeLabel = (authority) => {
  if (!isRepresentative(authority)) return "Собственник согласен стать поручителем по кредиту";
  if (authority.role === "OWNER") return "Я согласен(на) стать поручителем по кредиту";
  return "Подтверждаю, что согласие собственника на поручительство получено";
};

const STEP_TITLES = ["Цель", "Условия", "Выручка", "Согласия и отправка"];
const STEP2_FIELDS = ["amount", "term_months", "variant", "product_code", "collateral", "collateral.type", "collateral.description", "collateral.estimated_value", "owner_guarantee_consent", "inn", "authority.power_of_attorney", "authority.role"];
const innError = (inn) => (/^(\d{10}|\d{12})$/.test(inn) ? null : "Укажите 10 цифр для компании или 12 — для ИП");

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function toBody(f) {
  return {
    purpose: f.purpose,
    product_code: f.product_code,
    variant: f.variant,
    amount: Number(f.amount) || 0,
    term_months: Number(f.term_months) || 0,
    owner_guarantee_consent: !isSoleTrader(f.inn) && f.variant === "UNSECURED" ? !!f.owner_guarantee_consent : false,
    collateral: f.variant === "SECURED" ? { type: f.collateral.type, description: f.collateral.description, estimated_value: Number(f.collateral.estimated_value) || 0 } : null,
  };
}

export function ApplyPage() {
  const { user, company, selectCompany } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const editId = params.get("id");

  const dicts = useResource(() => Promise.all([api.purposes(), api.products(), api.collateralTypes(), editId ? api.application(editId) : null]), [editId]);
  const [step, setStep] = useState(1);
  const [appId, setAppId] = useState(editId || null);
  const [f, setF] = useState(null);
  const [statements, setStatements] = useState([]);
  const [errors, setErrors] = useState({});
  const [duplicate, setDuplicate] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!dicts.data || f) return;
    const [, , , app] = dicts.data;
    if (app) {
      setF({ ...app, has_account: !(app.statements || []).length, amount: String(app.amount || ""), term_months: String(app.term_months || ""), collateral: app.collateral || { type: "", description: "", estimated_value: "" } });
      setStatements(app.statements || []);
      return;
    }
    setF({
      inn: company?.inn || "",
      purpose: params.get("purpose") || "",
      product_code: "",
      variant: "UNSECURED",
      amount: "",
      term_months: "",
      owner_guarantee_consent: true,
      has_account: true,
      collateral: { type: "", description: "", estimated_value: "" },
    });
  }, [dicts.data]);

  if (!f || !dicts.data) return <PageSkeleton />;
  const [purposes, products, collateralTypes] = dicts.data;
  const set = (patch) => setF((x) => ({ ...x, ...patch }));
  const go = (n) => { setErrors({}); setStep(n); window.scrollTo(0, 0); };

  async function save() {
    const body = toBody(f);
    if (appId) return api.updateApplication(appId, body);
    const app = await api.createApplication({ inn: f.inn, ...body });
    setAppId(app.id);
    if (!user.companies?.some((c) => c.inn === f.inn)) selectCompany(f.inn);
    return app;
  }

  async function checkStep(fields, next) {
    setBusy(true);
    setDuplicate(null);
    try {
      const app = await save();
      const { errors: list = [] } = await api.validateApplication(app.id);
      const mine = Object.fromEntries(list.filter((e) => fields.includes(e.field)).map((e) => [e.field, e.message]));
      setErrors(mine);
      if (!Object.keys(mine).length) go(next);
    } catch (err) {
      if (err.status === 409) setDuplicate({ message: err.message, id: err.data?.error?.application_id || err.data?.application_id });
      else setErrors({ ...fieldErrors(err), form: err.status === 422 ? null : err.message });
    } finally {
      setBusy(false);
    }
  }

  const props = { f, set, errors, setErrors, busy, purposes, products, collateralTypes, go, appId, statements, setStatements, user, duplicate };

  return (
    <main className="page">
      <StepsHeader step={step} title={STEP_TITLES[step - 1]} />
      {step === 1 && <StepGoal {...props} />}
      {step === 2 && <StepTerms {...props} onNext={() => checkStep(STEP2_FIELDS, 3)} />}
      {step === 3 && <StepRevenue {...props} onNext={() => checkStep(["statements"], 4)} />}
      {step === 4 && <StepSubmit {...props} onDone={(id) => navigate(`/applications/${id}`)} />}
    </main>
  );
}

function StepGoal({ f, set, errors, setErrors, purposes, products, go, appId }) {
  const rec = useResource(() => (f.purpose ? api.recommendation(f.purpose) : null), [f.purpose]);
  const allowed = rec.data?.allowed || products.map((p) => p.code);
  const [checking, setChecking] = useState(false);
  const [needsAuthority, setNeedsAuthority] = useState(null);

  useEffect(() => {
    if (rec.data && !rec.data.allowed.includes(f.product_code)) set({ product_code: rec.data.recommended });
  }, [rec.data]);

  useEffect(() => setNeedsAuthority(null), [f.inn]);

  const proceed = (company) => {
    set({ authority: company.authority || null, ...(company.has_account != null ? { has_account: company.has_account } : {}) });
    go(2);
  };

  const next = async () => {
    const e = {};
    const inn = innError(f.inn);
    if (inn) e.inn = inn;
    if (!f.purpose) e.purpose = "Выберите, на что нужны деньги";
    if (!f.product_code) e.product = "Выберите продукт";
    setErrors(e);
    if (Object.keys(e).length) return;
    // Черновик тоже проверяем: доверенность могли отозвать, и тогда снова нужен шаг с ней.
    setChecking(true);
    try {
      const company = await api.company(f.inn);
      if (company.authority_required) { setNeedsAuthority(company); return; }
      if (!company.can_apply) { setErrors({ inn: company.cannot_apply_reason || "По этой компании подать заявку нельзя" }); return; }
      proceed(company);
    } catch (err) {
      setErrors({ inn: err.status === 404 ? "Компании с таким ИНН нет в ЕГРЮЛ и ЕГРИП" : errorText(err) });
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="grid grid--halves">
      <div className="stack stack-8">
        <Card>
          <div className="stack stack-4">
            <h2 className="h2">Компания</h2>
            <div style={{ maxWidth: 320 }}>
              <Field label="ИНН компании или ИП" variant="inn" value={f.inn} onChange={(v) => set({ inn: v })} hint="10 цифр для компании, 12 — для ИП" error={errors.inn} inputProps={{ readOnly: !!appId }} />
            </div>
          </div>
        </Card>
        {needsAuthority && <AuthorityCard company={needsAuthority} onVerified={proceed} />}
        <Card>
          <div role="radiogroup" aria-label="Цель" className="stack stack-2">
            <h2 className="h2">Нужны деньги на…</h2>
            <div className="radio-list">
              {purposes.map((p) => <RadioRow key={p.code} checked={f.purpose === p.code} onSelect={() => set({ purpose: p.code })}>{p.label}</RadioRow>)}
            </div>
            <FormError>{errors.purpose}</FormError>
          </div>
        </Card>
      </div>
      <div className="stack stack-8">
        {f.purpose && rec.data && (
          <Card>
            <div className="stack stack-4">
              <div className="stack stack-2">
                <h2 className="h2">{rec.data.title || `Рекомендуем: ${productName(rec.data.recommended).toLowerCase()}`}</h2>
                <p className="measure">{rec.data.explanation}</p>
              </div>
              <div role="radiogroup" aria-label="Продукт" className="radio-list radio-list--top">
                {[...products].sort((a, b) => (b.code === rec.data.recommended) - (a.code === rec.data.recommended)).map((p) => {
                  const ok = allowed.includes(p.code);
                  return (
                    <RadioRow key={p.code} checked={f.product_code === p.code} disabled={!ok} note={ok ? null : "Не подходит для этой цели"} onSelect={() => set({ product_code: p.code })}>
                      {p.name}
                    </RadioRow>
                  );
                })}
              </div>
              <FormError>{errors.product}</FormError>
            </div>
          </Card>
        )}
        {!needsAuthority && <div><Button onClick={next} loading={checking}>Продолжить</Button></div>}
      </div>
    </div>
  );
}

const POA_KINDS = [["MCHD", "МЧД"], ["NOTARIAL", "Нотариальная"], ["SIMPLE", "Простая письменная"]];

function AuthorityCard({ company, onVerified }) {
  const { reloadMe } = useAuth();
  const [role, setRole] = useState("");
  const [kind, setKind] = useState("MCHD");
  const [poa, setPoa] = useState({ mchd_guid: "", notarized_on: "", register_number: "", notary_name: "" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState(null);
  const setField = (k) => (v) => setPoa({ ...poa, [k]: v });

  const verify = async () => {
    const e = {};
    if (!role) e.role = "Выберите, кем вы приходитесь компании";
    if (kind === "MCHD" && !/^[0-9a-f-]{36}$/i.test(poa.mchd_guid.trim())) e.mchd_guid = "Номер МЧД — 36 символов, как в реестре ФНС";
    const notarizedOn = parseDateNumeric(poa.notarized_on);
    if (kind === "NOTARIAL") {
      if (!notarizedOn) e.notarized_on = "Дата в формате дд.мм.гггг";
      if (!poa.register_number.trim()) e.register_number = "Укажите реестровый номер";
      if (poa.notary_name.trim().length < 3) e.notary_name = "Укажите ФИО нотариуса";
    }
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    try {
      const body = kind === "MCHD"
        ? { kind, mchd_guid: poa.mchd_guid.trim() }
        : { kind, notarized_on: notarizedOn, register_number: poa.register_number.trim(), notary_name: poa.notary_name.trim() };
      const verified = await api.claimAuthority(company.inn, role, body);
      reloadMe();
      if (verified.authority?.registered_founder === false && owner === null) { setOwner(verified); return; }
      onVerified(verified);
    } catch (err) {
      const fe = fieldErrors(err);
      const poaError = fe["authority.power_of_attorney"];
      setErrors({ role: fe["authority.role"], poa: poaError, form: poaError || fe["authority.role"] ? null : errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  const isDirector = role === "DIRECTOR";
  return (
    <Card>
      <div className="stack stack-4">
        <h2 className="h2">Кем вы приходитесь компании?</h2>
        <p className="measure muted">По ЕГРЮЛ руководитель {company.director}. Подать заявку и подписать договор без доверенности может только он.</p>
        <div role="radiogroup" aria-label="Роль" className="radio-list">
          {APPLICANT_ROLES.map(([code, label]) => <RadioRow key={code} checked={role === code} onSelect={() => { setRole(code); setOwner(null); }}>{label}</RadioRow>)}
        </div>
        <FormError>{errors.role}</FormError>
        {isDirector && <Note>По ЕГРЮЛ руководитель {company.director}. Если это не вы, выберите свою роль и приложите доверенность.</Note>}
        {role && !isDirector && (
          <div className="stack stack-4">
            <h3 className="body-strong">Доверенность на подачу заявки и подписание кредитного договора</h3>
            <div role="radiogroup" aria-label="Вид доверенности" className="radio-list">
              {POA_KINDS.map(([code, label]) => <RadioRow key={code} checked={kind === code} onSelect={() => setKind(code)}>{label}</RadioRow>)}
            </div>
            {kind === "MCHD" && <Field label="Номер МЧД (GUID)" value={poa.mchd_guid} onChange={setField("mchd_guid")} error={errors.mchd_guid} placeholder="5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e01" />}
            {kind === "NOTARIAL" && (
              <>
                <Field label="Дата удостоверения" value={poa.notarized_on} onChange={setField("notarized_on")} error={errors.notarized_on} placeholder="дд.мм.гггг" />
                <Field label="Реестровый номер" value={poa.register_number} onChange={setField("register_number")} error={errors.register_number} placeholder="77/123-н/77-2026-4-512" />
                <Field label="ФИО нотариуса" value={poa.notary_name} onChange={setField("notary_name")} error={errors.notary_name} />
              </>
            )}
            {kind === "SIMPLE" && <Note>Простую доверенность мы не можем проверить онлайн. Оформите МЧД в личном кабинете ФНС — это бесплатно — и вернитесь с её номером.</Note>}
            <FormError>{errors.poa}</FormError>
          </div>
        )}
        {owner && <Note>Вас нет среди учредителей по ЕГРЮЛ. Доверенность действует, но проверьте выбранную роль.</Note>}
        <div className="stack stack-2 start">
          {owner
            ? <Button onClick={() => onVerified(owner)}>Продолжить</Button>
            : <Button onClick={verify} loading={busy} disabled={!role || isDirector || kind === "SIMPLE"}>Проверить доверенность</Button>}
          <FormError>{errors.form}</FormError>
        </div>
      </div>
    </Card>
  );
}

const range = (v) => `${formatMoney(v.amount_min)} – ${formatMoney(v.amount_max)}`;

function StepTerms({ f, set, errors, busy, products, collateralTypes, onNext, duplicate, go }) {
  const product = products.find((p) => p.code === f.product_code) || products[0];
  const variant = product.variants.find((v) => v.variant === f.variant) || product.variants[0];
  const payment = annuityPayment(f.amount, variant.rate_min, f.term_months);
  const company = !isSoleTrader(f.inn);
  const setCollateral = (patch) => set({ collateral: { ...f.collateral, ...patch } });

  return (
    <div className="grid grid--aside">
      <div className="stack stack-6">
        <div role="radiogroup" aria-label="Вариант" className="radio-cards">
          {product.variants.map((v) => (
            <RadioCard key={v.variant} checked={variant.variant === v.variant} onSelect={() => set({ variant: v.variant })} title={capitalize(variantName(v.variant))}>
              <span className="num">{range(v)}</span>
              <span className="small muted">{v.term_min}–{v.term_max} мес · ставка от {formatRate(v.rate_min)}</span>
            </RadioCard>
          ))}
        </div>
        <Card>
          <div className="stack stack-4">
            <div className="grid--2-1">
              <Field label="Сумма" variant="money" value={f.amount} onChange={(v) => set({ amount: v })} hint={`От ${formatMoney(variant.amount_min)} до ${formatMoney(variant.amount_max)}`} error={errors.amount} />
              <Field label="Срок, мес" variant="term" value={f.term_months} onChange={(v) => set({ term_months: v })} hint={`От ${variant.term_min} до ${variant.term_max}`} error={errors.term_months} />
            </div>
            {company && variant.variant === "UNSECURED" && (
              <Checkbox label={guaranteeLabel(f.authority)} checked={!!f.owner_guarantee_consent} onChange={(v) => set({ owner_guarantee_consent: v })} />
            )}
            {variant.variant === "SECURED" && (
              <div className="stack stack-4 divider-top" style={{ paddingTop: 16 }}>
                <span className="body-strong">Залог</span>
                <Field label="Тип залога" variant="select" options={collateralTypes.map((c) => ({ value: c.code, label: c.label }))} value={f.collateral.type} onChange={(v) => setCollateral({ type: v })} error={errors["collateral.type"] || errors.collateral} />
                <Field label="Описание" value={f.collateral.description} onChange={(v) => setCollateral({ description: v })} error={errors["collateral.description"]} />
                <div style={{ maxWidth: 360 }}>
                  <Field label="Оценочная стоимость" variant="money" value={f.collateral.estimated_value} onChange={(v) => setCollateral({ estimated_value: v })} error={errors["collateral.estimated_value"]} />
                </div>
              </div>
            )}
          </div>
        </Card>
        <div className="stack stack-4 start">
          <Button onClick={onNext} loading={busy}>Продолжить</Button>
          <FormError>{errors.form || errors.inn || errors.product_code || errors.owner_guarantee_consent}</FormError>
          {(errors["authority.power_of_attorney"] || errors["authority.role"]) && (
            <div className="stack stack-2 start">
              <FormError>{errors["authority.power_of_attorney"] || errors["authority.role"]}</FormError>
              <Button variant="link" onClick={() => go(1)}>Приложить действующую доверенность</Button>
            </div>
          )}
          {duplicate && (
            <Note>
              <p>{duplicate.message}</p>
              {duplicate.id && <Link to={`/applications/${duplicate.id}`}>Открыть заявку №{shortId(duplicate.id)}</Link>}
            </Note>
          )}
        </div>
      </div>
      <Card>
        <div className="stack stack-2">
          <span className="muted">Платёж в месяц</span>
          <Money value={payment} size="money" />
          <span className="small muted">По ставке от {formatRate(variant.rate_min)}{f.term_months ? ` на ${f.term_months} мес` : ""}</span>
        </div>
      </Card>
    </div>
  );
}

function StepRevenue({ f, set, errors, busy, appId, statements, setStatements, onNext }) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [deleting, setDeleting] = useState(null);

  const upload = async (file) => {
    setUploading(true);
    setUploadError(null);
    try {
      const s = await api.uploadStatement(appId, file);
      setStatements((list) => [...list, s]);
    } catch (err) {
      setUploadError(errorText(err));
    } finally {
      setUploading(false);
    }
  };
  const remove = async (s) => {
    setDeleting(s.id);
    try {
      await api.deleteStatement(appId, s.id);
      setStatements((list) => list.filter((x) => x.id !== s.id));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="stack stack-6 narrow">
      <div role="radiogroup" aria-label="Где ведёте бизнес" className="radio-cards">
        <RadioCard dot checked={f.has_account} onSelect={() => set({ has_account: true })} title="Веду бизнес в этом банке">
          <span className="small muted">Выручку посчитаем по оборотам вашего счёта</span>
        </RadioCard>
        <RadioCard dot checked={!f.has_account} onSelect={() => set({ has_account: false })} title="Веду бизнес в другом банке">
          <span className="small muted">Нужна выписка по счёту в формате 1С</span>
        </RadioCard>
      </div>
      {!f.has_account && (
        <Card>
          <div className="stack stack-4">
            <FileUpload hint="" checking={uploading} results={uploadError ? [{ type: "fail", text: uploadError }] : []} onSelect={upload} />
            {statements.length > 0 && <div>{statements.map((s) => <StatementRow key={s.id} statement={s} deleting={deleting === s.id} onDelete={() => remove(s)} />)}</div>}
            <FormError>{errors.statements}</FormError>
          </div>
        </Card>
      )}
      <div className="stack stack-4 start">
        <Button onClick={onNext} loading={busy}>Продолжить</Button>
        <FormError>{errors.form}</FormError>
      </div>
    </div>
  );
}

function StepSubmit({ f, purposes, products, user, appId, go, onDone }) {
  const { reloadMe } = useAuth();
  const consents = useResource(() => api.consents(f.inn), [f.inn]);
  const [checked, setChecked] = useState({});
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const required = useMemo(() => ["PERSONAL_DATA", ...(isSoleTrader(f.inn) ? [] : ["BKI_COMPANY"]), "BKI_PERSONAL"], [f.inn]);
  const given = consents.data || [];
  const missing = required.filter((t) => !given.some((g) => g.type === t));
  const companyName = user.companies?.find((c) => c.inn === f.inn)?.name;
  const consentLabels = isRepresentative(f.authority) ? { ...CONSENT_LABELS, ...REPRESENTATIVE_CONSENT_LABELS } : CONSENT_LABELS;
  const product = products.find((p) => p.code === f.product_code);

  const submit = async () => {
    if (missing.some((t) => !checked[t])) {
      setErrors({ consents: "Без этих согласий банк не сможет проверить кредитную историю" });
      return;
    }
    setBusy(true);
    setErrors({});
    try {
      if (missing.length) {
        await api.giveConsents(f.inn, missing);
        if (!user.companies?.some((c) => c.inn === f.inn)) reloadMe();
      }
      await api.submitApplication(appId);
      onDone(appId);
    } catch (err) {
      const fe = fieldErrors(err);
      const step2 = STEP2_FIELDS.find((k) => fe[k]);
      const authority = fe["authority.power_of_attorney"] || fe["authority.role"];
      setErrors({ terms: step2 ? fe[step2] : null, revenue: fe.statements, consents: fe.consents, authority, form: Object.keys(fe).length ? null : err.message });
    } finally {
      setBusy(false);
    }
  };

  const terms = [product?.name || productName(f.product_code), variantName(f.variant), formatMoney(f.amount), `${f.term_months} мес`].join(" · ");
  return (
    <div className="grid grid--7-5">
      <Card>
        <div className="stack">
          <div className="summary-row">
            <div className="stack stack-1"><span className="body-strong">Цель</span><span>{purposes.find((p) => p.code === f.purpose)?.label}</span></div>
            <Button variant="link" onClick={() => go(1)}>Изменить</Button>
          </div>
          <div className="summary-row">
            <div className="stack stack-1">
              <span className="body-strong">Условия</span>
              <span>{terms}</span>
              {f.variant === "UNSECURED" && !isSoleTrader(f.inn) && f.owner_guarantee_consent && <span className="muted">Собственник согласен стать поручителем</span>}
              {f.variant === "SECURED" && f.collateral?.description && <span className="muted">Залог: {f.collateral.description}, {formatMoney(f.collateral.estimated_value)}</span>}
              <FormError>{errors.terms}</FormError>
            </div>
            <Button variant="link" onClick={() => go(2)}>Изменить</Button>
          </div>
          <div className="summary-row">
            <div className="stack stack-1">
              <span className="body-strong">Компания и счёт</span>
              <span>{companyName ? `${companyName} · ИНН ${f.inn}` : `ИНН ${f.inn}`}</span>
              {errors.authority && (
                <>
                  <FormError>{errors.authority}</FormError>
                  <Button variant="link" onClick={() => go(1)}>Приложить действующую доверенность</Button>
                </>
              )}
              {f.authority && <span className="muted">Вы подаёте как: {roleName(f.authority.role).toLowerCase()}{f.authority.power_of_attorney ? `, ${f.authority.power_of_attorney}` : ""}</span>}
              <span className="muted">{f.has_account ? "Веду бизнес в этом банке" : "Веду бизнес в другом банке, выписка загружена"}</span>
              <FormError>{errors.revenue}</FormError>
            </div>
            <Button variant="link" onClick={() => go(3)}>Изменить</Button>
          </div>
        </div>
      </Card>
      <div className="stack stack-6">
        <Card>
          <div className="stack stack-4">
            <h2 className="h2">Согласия</h2>
            {given.filter((g) => required.includes(g.type)).map((g) => (
              <div key={g.type} className="note">
                <Icon name="circle-check" style={{ color: "var(--success)" }} />
                <div className="stack"><span>{consentLabels[g.type]}</span><span className="small muted">Дано {formatDateNumeric(g.given_at)}{g.given_by === user.id ? ", вами" : ""}</span></div>
              </div>
            ))}
            {missing.map((t) => (
              <Checkbox key={t} label={consentLabels[t]} checked={!!checked[t]} onChange={(v) => setChecked({ ...checked, [t]: v })} />
            ))}
            <FormError>{errors.consents}</FormError>
          </div>
        </Card>
        <div className="stack stack-4 start">
          <Button onClick={submit} loading={busy}>Отправить заявку</Button>
          <FormError>{errors.form}</FormError>
        </div>
      </div>
    </div>
  );
}
