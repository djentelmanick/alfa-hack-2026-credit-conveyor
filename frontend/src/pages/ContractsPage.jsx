import { Link, useNavigate } from "react-router-dom";
import { Card, DataTable, EmptyState, Icon, Skeleton, StatusBadge } from "../components/index.js";
import { api } from "../api/index.js";
import { useIsMobile, useResource } from "../lib/hooks.js";
import { formatDate, formatMoney } from "../lib/format.js";
import { productName, variantName } from "../lib/labels.js";

export const ContractBadge = ({ status }) => (status === "CLOSED" ? <StatusBadge kind="cancelled">Закрыт</StatusBadge> : <StatusBadge kind="success">Действует</StatusBadge>);

export function ContractsTable({ items, mobile, analyst = false }) {
  const navigate = useNavigate();
  const to = (c) => (analyst ? `/analyst/contracts/${c.id}` : `/contracts/${c.id}`);
  if (mobile) {
    return (
      <div>
        {items.map((c) => (
          <Link key={c.id} to={to(c)} className="app-line">
            <span className="stack stack-1">
              <span className="small muted">№{c.number} · {formatDate(c.contract_date)}</span>
              <span>{productName(c.product_code)} {variantName(c.variant)}</span>
              <span className="row row-2"><span className="body-strong num">{formatMoney(c.outstanding)}</span><ContractBadge status={c.status} /></span>
            </span>
            <Icon name="chevron-right" />
          </Link>
        ))}
      </div>
    );
  }
  const cols = [
    { key: "number", title: "Номер", render: (c) => `№${c.number}` },
    { key: "product", title: "Продукт", render: (c) => `${productName(c.product_code)} ${variantName(c.variant)}` },
    { key: "status", title: "Статус", render: (c) => <ContractBadge status={c.status} /> },
    { key: "outstanding", title: "Остаток долга", numeric: true, render: (c) => formatMoney(c.outstanding) },
    { key: "date", title: "Дата договора", render: (c) => formatDate(c.contract_date) },
  ];
  return <DataTable caption="Договоры" hideCaption columns={cols} rows={items} onRowClick={(c) => navigate(to(c))} />;
}

export function ContractsPage({ analyst = false }) {
  const mobile = useIsMobile();
  const { data, loading } = useResource(() => api.contracts(), []);
  return (
    <main className="page">
      <h1 className="h1">Договоры</h1>
      <Card>
        {loading ? <Skeleton lines={3} /> : data?.length ? <ContractsTable items={data} mobile={mobile} analyst={analyst} /> : <EmptyState title="Договоров пока нет" />}
      </Card>
    </main>
  );
}
