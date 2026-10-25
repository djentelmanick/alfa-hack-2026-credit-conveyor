import { useEffect, useId, useRef } from "react";
import { Button } from "./Button.jsx";
import { Icon } from "./Icon.jsx";

export function Chip({ selected = false, onClick, className = "", children, ...rest }) {
  return (
    <button type="button" aria-pressed={selected} className={"ck-chip" + (className ? " " + className : "")} onClick={onClick} {...rest}>
      {children}
    </button>
  );
}

export function Tabs({ tabs = [], value, onChange, label }) {
  return (
    <div className="ck-tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => {
        const text = t.label + (t.count != null ? " " + t.count : "");
        return (
          <button key={t.id} type="button" role="tab" className="ck-tab" data-label={text} aria-selected={t.id === value} tabIndex={t.id === value ? 0 : -1} onClick={() => onChange && onChange(t.id)}>
            {text}
          </button>
        );
      })}
    </div>
  );
}

const RESULT_KIND = { pass: ["circle-check", "var(--success)"], fail: ["circle-alert", "var(--danger)"], info: ["info", "var(--ink-muted)"] };

export function FileUpload({ label = "Выписка по счёту", hint = "Выгрузите выписку в интернет-банке в формате 1С — файл .txt", fileName, checking = false, results = [], onSelect, accept = ".txt" }) {
  const input = useRef(null);
  return (
    <div className="ck-upload">
      <span className="ck-upload__label">{label}</span>
      {hint && <span className="ck-upload__hint">{hint}</span>}
      <div className="ck-upload__row">
        <Button variant="secondary" disabled={checking} onClick={() => input.current && input.current.click()}>
          {fileName ? "Загрузить другую выписку" : "Загрузить выписку"}
        </Button>
        {fileName && <span className="ck-upload__file">{fileName}</span>}
        <input
          ref={input}
          type="file"
          accept={accept}
          className="ck-upload__input"
          tabIndex={-1}
          onChange={(e) => {
            const f = e.target.files && e.target.files[0];
            if (f && onSelect) onSelect(f);
            e.target.value = "";
          }}
        />
      </div>
      {checking ? (
        <ul className="ck-checks" aria-live="polite">
          <li><Icon name="loader-circle" spin /><span>Проверяем выписку</span></li>
        </ul>
      ) : results.length > 0 ? (
        <ul className="ck-checks" aria-live="polite">
          {results.map((r, i) => {
            const [icon, color] = RESULT_KIND[r.type] || RESULT_KIND.info;
            return (
              <li key={i} className={r.type === "fail" ? "ck-checks__fail" : undefined}>
                <Icon name={icon} style={{ color }} />
                <span>{r.text}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function Modal({ open = true, title, children, onClose, closeLabel = "Закрыть" }) {
  const ref = useRef(null);
  const tid = useId();
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement;
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    document.addEventListener("keydown", onKey);
    ref.current && ref.current.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      prev && prev.focus && prev.focus();
    };
  }, [open]);
  if (!open) return null;
  return (
    <div className="ck-modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}>
      <div ref={ref} className="ck-modal" role="dialog" aria-modal="true" aria-labelledby={tid} tabIndex={-1}>
        <h2 id={tid} className="h2">{title}</h2>
        <div className="ck-modal__body">{children}</div>
        <div className="ck-modal__foot"><Button variant="secondary" onClick={onClose}>{closeLabel}</Button></div>
      </div>
    </div>
  );
}
