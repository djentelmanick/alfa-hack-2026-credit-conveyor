export function Card({ variant = "default", as: Tag = "div", padding = true, className = "", children, ...rest }) {
  const cls = ["ck-card", variant !== "default" && "ck-card--" + variant, !padding && "ck-card--flush", className].filter(Boolean).join(" ");
  return <Tag className={cls} {...rest}>{children}</Tag>;
}
