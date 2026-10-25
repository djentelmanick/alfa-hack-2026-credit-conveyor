import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, DataTable, EmptyState, Field, Skeleton, StatusBadge, Tabs, statusMeta } from "../../components/index.js";
import { api } from "../../api/index.js";
import { useResource } from "../../lib/hooks.js";
import { formatDateTime, formatMoney } from "../../lib/format.js";
import { RISK_LABELS, productName, reasonLabel, shortId, variantName } from "../../lib/labels.js";

const SIZE = 20;
const STATUS_OPTIONS = ["SUBMITTED", "CHECKING", "NEEDS_INFO", "SCORING", "MANUAL_REVIEW", "APPROVED", "COUNTER_OFFER", "REJECTED", "SIGNING", "ISSUED", "CANCELLED"].map((s) => ({ value: s, label: statusMeta[s].label }));
const RISK_OPTIONS = Object.entries(RISK_LABELS).map(([value, label]) => ({ value, label: label.replace(" рейтинг", "") }));

const two = (a, b) => <span className="two-line"><span>{a}</span><span>{b}</span></span>;

const cols = [
  { key: "num", title: "Номер", width: 104, render: (r) => `№${shortId(r.id)}` },
  { key: "company", title: "Компания и ИНН", render: (r) => two(r.company_name, r.inn) },
  { key: "product", title: "Продукт и вариант", render: (r) => two(productName(r.product_code), variantName(r.variant)) },
  { key: "amount", title: "Сумма", numeric: true, render: (r) => formatMoney(r.amount) },
  { key: "status", title: "Статус", render: (r) => <StatusBadge status={r.status} /> },
  { key: "risk", title: "Риск", width: 56, render: (r) => r.risk_class || "—" },
  { key: "reasons", title: "Причины", render: (r) => <span className="stack">{(r.manual_reasons || []).map((t) => <span key={t} className="small">{reasonLabel(t)}</span>)}</span> },
  { key: "at", title: "Подана", render: (r) => <span style={{ whiteSpace: "nowrap" }}>{formatDateTime(r.submitted_at)}</span> },
];

export function QueuePage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState("all");
  const [filters, setFilters] = useState({ inn: "", status: "", risk_class: "" });
  const [page, setPage] = useState(1);
  const q = { ...filters, queue: tab === "manual" ? "manual" : undefined, page, size: SIZE };
  const { data, loading } = useResource(() => api.analystApplications(q), [tab, filters.inn, filters.status, filters.risk_class, page]);
  const setFilter = (k) => (v) => { setFilters({ ...filters, [k]: v }); setPage(1); };
  const pages = Math.max(1, Math.ceil((data?.total || 0) / SIZE));

  return (
    <main className="page page--tight">
      <h1 className="h1">Заявки</h1>
      <Tabs label="Очередь" tabs={[{ id: "manual", label: "Ручная проверка" }, { id: "all", label: "Все заявки" }]} value={tab} onChange={(t) => { setTab(t); setPage(1); }} />
      <div className="filters">
        <Field label="ИНН" variant="inn" value={filters.inn} onChange={setFilter("inn")} />
        <Field label="Статус" variant="select" placeholder="Все статусы" options={STATUS_OPTIONS} value={filters.status} onChange={setFilter("status")} />
        <Field label="Класс риска" variant="select" placeholder="Любой" options={RISK_OPTIONS} value={filters.risk_class} onChange={setFilter("risk_class")} />
      </div>
      <Card>
        <div className="stack stack-4">
          {loading && !data ? (
            <Skeleton lines={5} />
          ) : data?.items.length ? (
            <DataTable className="ck-table--rows48" caption="Заявки" hideCaption columns={cols} rows={data.items} onRowClick={(r) => navigate(`/analyst/applications/${r.id}`)} />
          ) : (
            <EmptyState title={tab === "manual" ? "Заявок на ручной проверке нет" : "Заявок не найдено"} />
          )}
          <div className="pager">
            <span className="small muted">Страница {page} из {pages}</span>
            <div className="row row-2">
              <Button variant="secondary" compact disabled={page <= 1} onClick={() => setPage(page - 1)}>Предыдущая страница</Button>
              <Button variant="secondary" compact disabled={page >= pages} onClick={() => setPage(page + 1)}>Следующая страница</Button>
            </div>
          </div>
        </div>
      </Card>
    </main>
  );
}
