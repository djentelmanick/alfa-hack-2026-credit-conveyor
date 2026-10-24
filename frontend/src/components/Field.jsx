import { useId, useState } from "react";
import { Icon } from "./Icon.jsx";

const group = (d) => d.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
const LIMIT = { inn: 12, term: 3, money: 15 };

/** Поле ввода: текст, ИНН, сумма, срок или select. Значение суммы хранится без пробелов. */
export function Field({ label, hint, error, variant = "text", value, defaultValue = "", onChange, options = [], placeholder, disabled = false, id, name, inputProps = {}, className = "" }) {
  const [inner, setInner] = useState(String(defaultValue ?? ""));
  const autoId = useId();
  const fid = id || autoId;
  const raw = value !== undefined ? String(value ?? "") : inner;
  const set = (v) => {
    if (value === undefined) setInner(v);
    onChange && onChange(v);
  };
  const numeric = variant in LIMIT;
  const shown = variant === "money" ? group(raw.replace(/\D/g, "")) : raw;
  const suffix = variant === "money" ? "₽" : variant === "term" ? "мес." : null;
  const msgId = fid + "-msg";
  const aria = { "aria-invalid": error ? true : undefined, "aria-describedby": error || hint ? msgId : undefined };
  const cls = ["ck-field", error && "ck-field--error", disabled && "ck-field--disabled", className].filter(Boolean).join(" ");

  return (
    <div className={cls}>
      <label htmlFor={fid} className="ck-field__label">{label}</label>
      <div className="ck-field__control">
        {variant === "select" ? (
          <>
            <select id={fid} name={name} className="ck-field__input" value={raw} disabled={disabled} onChange={(e) => set(e.target.value)} {...aria} {...inputProps}>
              {placeholder !== null && <option value="">{placeholder || "Выберите"}</option>}
              {options.map((o) => {
                const v = typeof o === "string" ? o : o.value;
                return <option key={v} value={v}>{typeof o === "string" ? o : o.label}</option>;
              })}
            </select>
            <Icon name="chevron-down" className="ck-field__chevron" />
          </>
        ) : (
          <input
            id={fid}
            name={name}
            type="text"
            value={shown}
            disabled={disabled}
            placeholder={placeholder}
            className={"ck-field__input" + (numeric ? " ck-field__input--num" : "")}
            inputMode={numeric ? "numeric" : undefined}
            autoComplete="off"
            onChange={(e) => set(numeric ? e.target.value.replace(/\D/g, "").slice(0, LIMIT[variant]) : e.target.value)}
            {...aria}
            {...inputProps}
          />
        )}
        {suffix && <span className="ck-field__suffix" aria-hidden="true">{suffix}</span>}
      </div>
      {error ? (
        <p id={msgId} className="ck-field__error"><Icon name="circle-alert" /><span>{error}</span></p>
      ) : hint ? (
        <p id={msgId} className="ck-field__hint">{hint}</p>
      ) : null}
    </div>
  );
}
