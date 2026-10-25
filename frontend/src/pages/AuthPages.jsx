import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Button, Card, Field, FormError } from "../components/index.js";
import { api, fieldErrors } from "../api/index.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { parseDateNumeric } from "../lib/format.js";

const homeFor = (user) => (user.role === "ANALYST" ? "/analyst" : "/");

export function LoginPage() {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ login: location.state?.login || "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (loading) return null;
  if (user) return <Navigate to={homeFor(user)} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await login(form.login.trim(), form.password);
      const from = location.state?.from;
      navigate(from && me.role === "CLIENT" && !from.startsWith("/analyst") ? from : homeFor(me), { replace: true });
    } catch (err) {
      setError(err.status === 401 ? "Неверный логин или пароль" : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <div className="auth__box">
        <h1 className="h1 auth__title">Кредитный кабинет</h1>
        <Card>
          <form className="stack stack-4" onSubmit={submit} noValidate>
            <Field label="Логин" value={form.login} onChange={(v) => setForm({ ...form, login: v })} inputProps={{ autoComplete: "username" }} />
            <Field label="Пароль" value={form.password} onChange={(v) => setForm({ ...form, password: v })} inputProps={{ type: "password", autoComplete: "current-password" }} />
            <Button type="submit" block loading={busy}>Войти</Button>
            <FormError>{error}</FormError>
          </form>
        </Card>
        <div className="auth__switch"><Link to="/register">Зарегистрироваться</Link></div>
      </div>
    </main>
  );
}

export function RegisterPage() {
  const navigate = useNavigate();
  const [f, setF] = useState({ full_name: "", login: "", password: "", series: "", number: "", issued_at: "" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF({ ...f, [k]: v });

  const submit = async (e) => {
    e.preventDefault();
    const local = {};
    const issuedAt = parseDateNumeric(f.issued_at);
    if (f.full_name.trim().length < 3) local.full_name = "Укажите фамилию, имя и отчество";
    if (!/^[A-Za-z0-9._@-]{3,64}$/.test(f.login.trim())) local.login = "Логин — от 3 символов: латиница, цифры, точка, _, @, -";
    if (f.password.length < 8) local.password = "Пароль — не короче 8 символов";
    if (!/^\d{4}$/.test(f.series) || !/^\d{6}$/.test(f.number)) local.passport = "Серия — 4 цифры, номер — 6 цифр";
    else if (!issuedAt) local.passport = "Укажите дату выдачи в формате дд.мм.гггг";
    setErrors(local);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      await api.register({ login: f.login.trim(), password: f.password, full_name: f.full_name.trim(), passport: { series: f.series, number: f.number, issued_at: issuedAt } });
      navigate("/login", { state: { login: f.login.trim() } });
    } catch (err) {
      const fe = fieldErrors(err);
      if (err.status === 409) setErrors({ login: fe.login || "Логин занят" });
      else if (err.status === 422) {
        const passport = Object.entries(fe).find(([k]) => k.startsWith("passport"))?.[1];
        setErrors({ ...fe, passport: passport || (Object.keys(fe).length ? null : err.message || "Паспорт недействителен — проверьте серию, номер и дату выдачи") });
      }
      else setErrors({ form: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth auth--register">
      <div className="auth__box">
        <h1 className="h1 auth__title">Регистрация</h1>
        <Card>
          <form className="stack stack-4" onSubmit={submit} noValidate>
            <Field label="ФИО" value={f.full_name} onChange={set("full_name")} error={errors.full_name} inputProps={{ autoComplete: "name" }} />
            <Field label="Логин" value={f.login} onChange={set("login")} error={errors.login} inputProps={{ autoComplete: "username" }} />
            <Field label="Пароль" value={f.password} onChange={set("password")} error={errors.password} inputProps={{ type: "password", autoComplete: "new-password" }} />
            <fieldset className="stack stack-4" style={{ border: 0, margin: 0, padding: "8px 0 0" }}>
              <legend className="body-strong" style={{ padding: 0, marginBottom: 16 }}>Паспорт</legend>
              <div className="grid--passport">
                <Field label="Серия" value={f.series} onChange={(v) => set("series")(v.replace(/\D/g, "").slice(0, 4))} inputProps={{ inputMode: "numeric" }} />
                <Field label="Номер" value={f.number} onChange={(v) => set("number")(v.replace(/\D/g, "").slice(0, 6))} inputProps={{ inputMode: "numeric" }} />
              </div>
              <Field label="Дата выдачи" value={f.issued_at} onChange={set("issued_at")} placeholder="дд.мм.гггг" />
              <FormError>{errors.passport}</FormError>
            </fieldset>
            <Button type="submit" block loading={busy}>Зарегистрироваться</Button>
            <FormError>{errors.form}</FormError>
          </form>
        </Card>
        <div className="auth__switch"><Link to="/login">Войти</Link></div>
      </div>
    </main>
  );
}
