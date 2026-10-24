import { useId, useState } from "react";
import { Icon } from "./Icon.jsx";
import { Button } from "./Button.jsx";

export function Checkbox({ label, description, consentLabel = "Текст согласия", onConsentClick, error, checked, defaultChecked = false, onChange, disabled = false, id, name, className = "" }) {
  const [inner, setInner] = useState(!!defaultChecked);
  const autoId = useId();
  const fid = id || autoId;
  const on = checked !== undefined ? checked : inner;
  const cls = ["ck-check", error && "ck-check--error", disabled && "ck-check--disabled", className].filter(Boolean).join(" ");
  return (
    <div className={cls}>
      <input
        id={fid}
        name={name}
        type="checkbox"
        className="ck-check__input"
        checked={on}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? fid + "-err" : undefined}
        onChange={(e) => {
          if (checked === undefined) setInner(e.target.checked);
          onChange && onChange(e.target.checked);
        }}
      />
      <span className="ck-check__box" aria-hidden="true"><Icon name="check" size={14} strokeWidth={2.5} /></span>
      <div className="ck-check__content">
        <label htmlFor={fid} className="ck-check__label">{label}</label>
        {description && <span className="ck-check__desc">{description}</span>}
        {onConsentClick && <Button variant="link" onClick={onConsentClick}>{consentLabel}</Button>}
        {error && <span id={fid + "-err"} className="ck-check__error"><Icon name="circle-alert" /><span>{error}</span></span>}
      </div>
    </div>
  );
}
