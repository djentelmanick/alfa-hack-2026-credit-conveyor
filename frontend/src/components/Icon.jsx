const circle = ["circle", { cx: 12, cy: 12, r: 10 }];

/** Lucide (ISC). Только функциональные иконки продукта. */
export const iconPaths = {
  "clock": [circle, ["path", { d: "M12 6v6l4 2" }]],
  "circle-alert": [circle, ["line", { x1: 12, x2: 12, y1: 8, y2: 12 }], ["line", { x1: 12, x2: 12.01, y1: 16, y2: 16 }]],
  "circle-check": [circle, ["path", { d: "m9 12 2 2 4-4" }]],
  "circle-minus": [circle, ["path", { d: "M8 12h8" }]],
  "pencil-line": [["path", { d: "M13 21h8" }], ["path", { d: "m15 5 4 4" }], ["path", { d: "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" }]],
  "ban": [circle, ["path", { d: "M4.929 4.929 19.07 19.071" }]],
  "info": [circle, ["path", { d: "M12 16v-4" }], ["path", { d: "M12 8h.01" }]],
  "loader-circle": [["path", { d: "M21 12a9 9 0 1 1-6.219-8.56" }]],
  "chevron-down": [["path", { d: "m6 9 6 6 6-6" }]],
  "chevron-right": [["path", { d: "m9 18 6-6-6-6" }]],
  "check": [["path", { d: "M20 6 9 17l-5-5" }]],
};

export function Icon({ name, size = 20, strokeWidth = 1.75, label, spin = false, className = "", style }) {
  const parts = iconPaths[name];
  if (!parts) return null;
  const cls = ["ck-icon", spin && "ck-spin", className].filter(Boolean).join(" ");
  return (
    <svg
      className={cls}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      style={style}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {parts.map(([Tag, attrs], i) => <Tag key={i} {...attrs} />)}
    </svg>
  );
}
