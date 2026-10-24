import { useId, useState } from "react";
import { Icon } from "./Icon.jsx";

export function Disclosure({ title, level = "h2", defaultOpen = false, plain = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const pid = useId();
  const Tag = level === "h2" ? "h2" : "h3";
  return (
    <section className={"ck-disc" + (plain ? " ck-disc--plain" : "")}>
      <Tag style={{ margin: 0, font: "inherit" }}>
        <button type="button" className={"ck-disc__btn ck-disc__btn--" + level} aria-expanded={open} aria-controls={pid} onClick={() => setOpen(!open)}>
          <span>{title}</span>
          <Icon name="chevron-down" />
        </button>
      </Tag>
      {open && <div id={pid} className="ck-disc__panel">{children}</div>}
    </section>
  );
}
