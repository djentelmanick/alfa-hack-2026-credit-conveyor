import { Link } from "react-router-dom";
import { Icon } from "./Icon.jsx";

const cx = (...a) => a.filter(Boolean).join(" ");

export function Button({ variant = "primary", compact = false, block = false, loading = false, disabled = false, icon, type = "button", className = "", onClick, children, ...rest }) {
  return (
    <button
      type={type}
      disabled={disabled}
      aria-busy={loading || undefined}
      className={cx("ck-btn", "ck-btn--" + variant, compact && "ck-btn--compact", block && "ck-btn--block", className)}
      onClick={(e) => {
        if (loading) {
          e.preventDefault();
          return;
        }
        onClick && onClick(e);
      }}
      {...rest}
    >
      {icon && <Icon name={icon} />}
      {children}
      {loading && <Icon name="loader-circle" spin />}
    </button>
  );
}

export function ButtonLink({ variant = "secondary", compact = false, block = false, to = "/", className = "", children, ...rest }) {
  return (
    <Link to={to} className={cx("ck-btn", "ck-btn--" + variant, compact && "ck-btn--compact", block && "ck-btn--block", className)} {...rest}>
      {children}
    </Link>
  );
}
