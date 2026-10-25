import { Icon } from "./Icon.jsx";

export const statusMeta = {
  DRAFT: { kind: "draft", label: "Черновик" },
  SUBMITTED: { kind: "processing", label: "Отправлена" },
  CHECKING: { kind: "processing", label: "На проверке" },
  NEEDS_INFO: { kind: "waiting", label: "Нужны данные" },
  SCORING: { kind: "processing", label: "На оценке" },
  MANUAL_REVIEW: { kind: "processing", label: "У специалиста" },
  APPROVED: { kind: "success", label: "Одобрена" },
  COUNTER_OFFER: { kind: "waiting", label: "Есть предложение" },
  REJECTED: { kind: "rejected", label: "Отказ" },
  SIGNING: { kind: "waiting", label: "Подписание" },
  ISSUED: { kind: "success", label: "Выдано" },
  CANCELLED: { kind: "cancelled", label: "Отменена" },
};

const BADGE_ICON = { processing: "clock", waiting: "circle-alert", success: "circle-check", rejected: "circle-minus", draft: "pencil-line", cancelled: "ban" };

export function StatusBadge({ status, kind, children }) {
  const meta = status ? statusMeta[status] : null;
  const k = kind || (meta && meta.kind) || "processing";
  return (
    <span className={"ck-badge ck-badge--" + k}>
      <Icon name={BADGE_ICON[k]} />
      {children ?? (meta && meta.label)}
    </span>
  );
}

export const stations = ["Подана", "Проверка", "Оценка", "Решение", "Выдача"];

export const statusLineMap = {
  SUBMITTED: [0, "current", "Заявка у нас, начинаем проверку"],
  CHECKING: [1, "current", "Проверяем компанию в реестрах и кредитную историю. Обычно 1–2 минуты"],
  NEEDS_INFO: [1, "waiting", "Банку не хватает данных"],
  SCORING: [2, "current", "Считаем риск. Обычно 1–2 минуты"],
  MANUAL_REVIEW: [2, "current", "Заявку смотрит специалист. Обычно в течение дня"],
  APPROVED: [3, "waiting", "Банк одобрил заявку"],
  COUNTER_OFFER: [3, "waiting", "Банк предлагает другие условия"],
  REJECTED: [3, "stopped", "Банк не может выдать кредит"],
  SIGNING: [4, "waiting", "Подпишите договор"],
  ISSUED: [4, "done", "Деньги зачислены"],
};

const SR = { done: "пройдено", current: "сейчас здесь", waiting: "ждём вас", future: "впереди", stopped: "остановлено" };

export function StatusLine({ status, caption, action, orientation = "responsive", label = "Путь заявки", stop, at: atOverride }) {
  const m = statusLineMap[status];
  if (!m) return null;
  const [baseAt, type, defCaption] = m;
  const stopped = type === "stopped";
  const at = atOverride ?? (stopped && stop !== undefined ? stop : baseAt);
  const list = stopped ? stations.slice(0, at + 1) : stations;
  const kind = (i) => (i < at ? "done" : i === at ? type : "future");
  const text = caption === undefined ? defCaption : caption;
  return (
    <div className={"ck-sl ck-sl--" + orientation + (stopped ? " ck-sl--stopped" : "")}>
      <ol className="ck-sl__track" aria-label={label}>
        {list.map((name, i) => {
          const t = kind(i);
          const last = i === list.length - 1;
          return (
            <li key={name} className={"ck-sl__st ck-sl__st--" + t} aria-current={t === "current" || t === "waiting" || t === "stopped" ? "step" : undefined}>
              <span className="ck-sl__rail" aria-hidden="true">
                <span className="ck-sl__node">
                  <span className="ck-sl__dot">{t === "waiting" && <Icon name="circle-alert" size={16} strokeWidth={2} />}</span>
                </span>
                {!last && <span className={"ck-sl__seg" + (t === "done" ? "" : " ck-sl__seg--dashed")} />}
              </span>
              <span className="ck-sl__label">
                {name}
                <span className="visually-hidden">, {SR[t]}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {text && <p className="ck-sl__caption" aria-live="polite">{text}</p>}
      {action && <div className="ck-sl__action">{action}</div>}
    </div>
  );
}
