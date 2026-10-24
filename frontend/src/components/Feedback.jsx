import { useEffect } from "react";
import { Button } from "./Button.jsx";

export function Toast({ message, open = true, onClose, duration = 4000 }) {
  useEffect(() => {
    if (!open || !duration) return;
    const t = setTimeout(() => onClose && onClose(), duration);
    return () => clearTimeout(t);
  }, [open, message]);
  if (!open || !message) return null;
  return <div className="ck-toast" role="status" aria-live="polite">{message}</div>;
}

export function NetworkBanner({ message = "Нет связи с сервером. Повторяем…", onRetry, retryLabel = "Повторить" }) {
  return (
    <div className="ck-netbanner" role="status">
      <span>{message}</span>
      <Button variant="secondary" compact onClick={onRetry}>{retryLabel}</Button>
    </div>
  );
}

export function EmptyState({ title, text, action }) {
  return (
    <div className="ck-empty">
      <h2 className="h2">{title}</h2>
      {text && <p className="ck-empty__text">{text}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ variant = "text", lines = 3, width, height, style }) {
  if (variant === "text") {
    return (
      <div className="ck-skel-lines" aria-hidden="true" style={{ width, ...style }}>
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className="ck-skel ck-skel--text" style={{ width: i === lines - 1 && lines > 1 ? "60%" : "100%", height }} />
        ))}
      </div>
    );
  }
  return <span aria-hidden="true" className={"ck-skel ck-skel--" + variant} style={{ width: width ?? "100%", height, ...style }} />;
}
