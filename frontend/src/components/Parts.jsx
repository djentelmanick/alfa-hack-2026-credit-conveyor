import { Icon } from "./Icon.jsx";
import { Button } from "./Button.jsx";
import { formatPeriod, plural } from "../lib/format.js";

/** Строка-радио в списке (цель, продукт). */
export function RadioRow({ checked, disabled, onSelect, children, note }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={!!checked}
      aria-disabled={disabled || undefined}
      className="radio-row"
      onClick={() => !disabled && onSelect && onSelect()}
    >
      <span className={"radio-dot" + (checked ? " is-on" : disabled ? " is-off" : "")} />
      {note ? (
        <span className="stack muted"><span>{children}</span><span className="small">{note}</span></span>
      ) : (
        <span>{children}</span>
      )}
    </button>
  );
}

/** Крупный выбор карточкой: вариант продукта, где ведёте бизнес. */
export function RadioCard({ checked, onSelect, dot = false, title, children }) {
  return (
    <button type="button" role="radio" aria-checked={!!checked} className="radio-card" onClick={onSelect}>
      {dot && <span className={"radio-dot" + (checked ? " is-on" : "")} />}
      <span className={"stack " + (dot ? "stack-1" : "stack-2")}>
        <span className="body-strong">{title}</span>
        {children}
      </span>
    </button>
  );
}

export function StepsHeader({ step, total = 4, title }) {
  return (
    <div className="steps">
      <h1 className="h1">Шаг {step} из {total} · {title}</h1>
      <div className="steps__bar" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => <span key={i} className={i < step ? "is-done" : undefined} />)}
      </div>
    </div>
  );
}

/** Сообщение «нужно ваше действие» с янтарной иконкой. */
export function Note({ children }) {
  return (
    <div className="note note--attention">
      <Icon name="circle-alert" />
      <div className="stack stack-1 start">{children}</div>
    </div>
  );
}

export function FormError({ children }) {
  if (!children) return null;
  return <p className="form-error" role="alert"><Icon name="circle-alert" />{children}</p>;
}

const SIGNATURE = { VALID: "есть, проверена", INVALID: "есть, не прошла проверку", MISSING: "нет" };

export function StatementRow({ statement, onDelete, deleting }) {
  const s = statement;
  const ok = s.signature_status === "VALID";
  return (
    <div className="statement">
      <div className="stack stack-1">
        <span className="body-strong">{s.bank_name} · {formatPeriod(s.period_from, s.period_to)}</span>
        <span className="muted">{s.months_covered} {plural(s.months_covered, "месяц", "месяца", "месяцев")} · операций {s.operations_count}</span>
        <span className="ok-line">
          <Icon name={ok ? "circle-check" : "circle-alert"} style={ok ? undefined : { color: "var(--attention)" }} />
          Подпись банка: {SIGNATURE[s.signature_status] || "нет"}
        </span>
      </div>
      {onDelete && <div><Button variant="link" loading={deleting} onClick={onDelete}>Удалить</Button></div>}
    </div>
  );
}
