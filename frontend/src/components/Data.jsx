import { Fragment } from "react";
import { formatMoney } from "../lib/format.js";

export function DataTable({ caption, hideCaption = false, columns = [], rows = [], onRowClick, rowKey = "id", className = "" }) {
  return (
    <div className={"ck-table" + (className ? " " + className : "")}>
      {caption && !hideCaption && <div className="ck-table__caption" aria-hidden="true">{caption}</div>}
      <div className="ck-table__scroll">
        <table>
          {caption && <caption className="visually-hidden">{caption}</caption>}
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} scope="col" className={c.numeric ? "is-num" : undefined} style={c.width ? { width: c.width } : undefined}>{c.title}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={r[rowKey] ?? i}
                className={onRowClick ? "is-link" : undefined}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.numeric ? "is-num" : undefined}>{c.render ? c.render(r) : r[c.key]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function KeyValue({ items = [], source }) {
  return (
    <div className="ck-kv">
      <dl className="ck-kv__list">
        {items.map((it, i) => (
          <Fragment key={i}>
            <dt>{it.label}</dt>
            <dd>{it.value}</dd>
          </Fragment>
        ))}
      </dl>
      {source && <p className="ck-kv__source">Источник: {source}</p>}
    </div>
  );
}

export function AnalystComment({ children, author = "Специалист банка" }) {
  return (
    <figure className="ck-quote">
      <blockquote className="ck-quote__text">{children}</blockquote>
      <figcaption className="ck-quote__by">{author}</figcaption>
    </figure>
  );
}

export function History({ items = [] }) {
  return (
    <ol className="ck-history">
      {items.map((it, i) => (
        <li key={i} className="ck-history__item">
          <time className="ck-history__time">{it.time}</time>
          <div className="ck-history__main">
            <span className="ck-history__event">{it.event}</span>
            {it.actor && <span className="ck-history__actor">{it.actor}</span>}
            {it.comment && <AnalystComment>{it.comment}</AnalystComment>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Money({ value, size = "body", currency = true, as: Tag = "span", className = "" }) {
  return <Tag className={"ck-money ck-money--" + size + (className ? " " + className : "")}>{formatMoney(value, currency)}</Tag>;
}
