import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ButtonLink, Card, Chip, DataTable, Disclosure, EmptyState, Icon, KeyValue, Skeleton, StatusBadge, StatusLine } from "../components/index.js";
import { api } from "../api/index.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useIsMobile, useResource } from "../lib/hooks.js";
import { firstName, formatDate, formatDateTime, formatMoney } from "../lib/format.js";
import { COMPANY_STATUS, HOME_PHRASE, isActive, productLine, productName, shortId, variantName } from "../lib/labels.js";
import { ContractsTable } from "./ContractsPage.jsx";

export function companyItems(c) {
  const rows = [
    ["Название", c.name],
    ["ИНН", c.inn],
    [c.kind === "IE" ? "ОГРНИП" : "ОГРН", c.ogrn],
    ["Дата регистрации", formatDate(c.registered_at)],
    ["Статус", COMPANY_STATUS[c.status] || c.status],
    ["ОКВЭД", c.okved && `${c.okved.code} ${c.okved.label}`],
    ["Руководитель", c.director],
    ["Учредители", (c.founders || []).map((f) => `${f.name} — ${String(f.share_pct).replace(".", ",")}%`).join(", ")],
    ["Адрес", c.address],
  ];
  return rows.filter(([, v]) => v).map(([label, value]) => ({ label, value }));
}

const productShort = (a) => `${productName(a.product_code)} ${variantName(a.variant)}`;

function ApplicationsTable({ items, mobile, showCompany }) {
  const navigate = useNavigate();
  if (mobile) {
    return (
      <div>
        {items.map((a) => (
          <Link key={a.id} to={`/applications/${a.id}`} className="app-line">
            <span className="stack stack-1">
              <span className="small muted">№{shortId(a.id)} · обновлена {formatDateTime(a.updated_at)}</span>
              <span>{productShort(a)}</span>
              <span className="row row-2"><span className="body-strong num">{formatMoney(a.amount)}</span><StatusBadge status={a.status} /></span>
            </span>
            <Icon name="chevron-right" />
          </Link>
        ))}
      </div>
    );
  }
  const cols = [
    { key: "num", title: "Номер", render: (a) => `№${shortId(a.id)}` },
    ...(showCompany ? [{ key: "company", title: "Компания", render: (a) => a.company_name }] : []),
    { key: "product", title: "Продукт", render: productShort },
    { key: "amount", title: "Сумма", numeric: true, render: (a) => formatMoney(a.amount) },
    { key: "status", title: "Статус", render: (a) => <StatusBadge status={a.status} /> },
    { key: "upd", title: "Обновлена", render: (a) => <span className="num">{formatDateTime(a.updated_at)}</span> },
  ];
  return <DataTable caption="Мои заявки" hideCaption columns={cols} rows={items} onRowClick={(a) => navigate(`/applications/${a.id}`)} />;
}

function HomeSkeleton() {
  return (
    <main className="page" aria-busy="true" aria-label="Загрузка">
      <Skeleton variant="text" lines={1} width="60%" height={40} />
      <Card><div className="stack stack-6"><Skeleton variant="text" lines={1} width="70%" height={26} /><Skeleton variant="control" /><Skeleton variant="text" lines={1} width="30%" /></div></Card>
      <div className="grid grid--7-5">
        <Card><Skeleton lines={3} /></Card>
        <Card><Skeleton lines={6} /></Card>
      </div>
    </main>
  );
}

export function HomePage() {
  const { user, company } = useAuth();
  const mobile = useIsMobile();
  const navigate = useNavigate();
  const inn = company?.inn;
  const apps = useResource(() => api.applications(), []);
  const contracts = useResource(() => api.contracts(), []);
  const purposes = useResource(() => api.purposes(), []);
  const companyRes = useResource(() => (inn ? api.company(inn).catch(() => null) : null), [inn]);

  const items = (apps.data?.items || []).filter((a) => !inn || a.inn === inn);
  const active = items.find((a) => isActive(a.status));
  const activeId = active?.id;
  const moving = ["SUBMITTED", "CHECKING", "SCORING", "MANUAL_REVIEW"].includes(active?.status);

  const [step, setStep] = useState(null);

  useEffect(() => {
    if (!activeId || !moving) return;
    return api.subscribeStatus(activeId, (msg) => {
      if (msg.type === "application.step") setStep(msg.label);
      else apps.reload({ silent: true });
    });
  }, [activeId, moving]);

  if (apps.loading && !apps.data) return <HomeSkeleton />;

  const name = firstName(user.full_name);
  const status = active?.status;
  const phrase = active ? `${name}, ${HOME_PHRASE[status] || "заявка в работе"}` : `${name}, активных заявок нет`;
  const companyData = companyRes.data;
  const myContracts = contracts.data || [];
  const draft = status === "DRAFT";

  const applyCard = active && (
    <Card variant="raised">
      <div className={"stack " + (mobile ? "stack-4" : "stack-6")}>
        {mobile ? (
          <div className="stack stack-1">
            <span className="small muted">Заявка №{shortId(active.id)}</span>
            <span className="body-strong">{productLine(active, { term: false })}</span>
          </div>
        ) : (
          <h2 className="h2">Заявка №{shortId(active.id)} · {productLine(active, { term: false })}</h2>
        )}
        {draft ? (
          <p className="muted">Анкета заполнена не до конца — заявка ещё не в банке</p>
        ) : (
          <StatusLine status={status} caption={status === "CHECKING" && step ? step : undefined} orientation={mobile ? "vertical" : "horizontal"} />
        )}
        <div>
          {draft ? <Link to={`/apply?id=${active.id}`}>Продолжить анкету</Link> : <Link to={`/applications/${active.id}`}>Открыть заявку</Link>}
        </div>
      </div>
    </Card>
  );

  const goals = (
    <section className="stack stack-4">
      <h2 className="h2">Нужны деньги на…</h2>
      <div className="ck-chips">
        {(purposes.data || []).map((p) => (
          <Chip key={p.code} disabled={!!active} onClick={() => navigate(`/apply?purpose=${p.code}`)}>{p.label}</Chip>
        ))}
      </div>
      {active && <p className="muted">Новую заявку по этой компании можно подать, когда закончится текущая</p>}
    </section>
  );

  const appsCard = (
    <Card>
      <div className="stack stack-2">
        <h2 className="h2">Мои заявки</h2>
        {items.length ? <ApplicationsTable items={items} mobile={mobile} showCompany={(user.companies || []).length > 1} /> : <EmptyState title="Заявок пока нет" action={<ButtonLink to="/apply">Подобрать кредит</ButtonLink>} />}
      </div>
    </Card>
  );

  const contractsCard = (
    <Card>
      <div className="stack stack-2">
        <h2 className="h2">Договоры</h2>
        {myContracts.length ? <ContractsTable items={myContracts} mobile={mobile} /> : <p className="muted">Договоров пока нет</p>}
      </div>
    </Card>
  );

  const companyCard = companyData && (
    mobile ? (
      <Card>
        <Disclosure title="Компания" plain>
          <KeyValue items={companyItems(companyData)} source="ЕГРЮЛ" />
        </Disclosure>
      </Card>
    ) : (
      <Card>
        <div className="stack stack-2">
          <div className="row row-4 row--between row--baseline"><h2 className="h2">Компания</h2><span className="small muted">Из ЕГРЮЛ</span></div>
          <KeyValue items={companyItems(companyData)} />
        </div>
      </Card>
    )
  );

  return (
    <main className="page">
      <h1 className="display">{phrase}</h1>
      {applyCard}
      {goals}
      {mobile ? (
        <>{appsCard}{contractsCard}{companyCard}</>
      ) : (
        <div className={"grid " + (companyCard ? "grid--7-5" : "")}>
          <div className="stack stack-8">{appsCard}{contractsCard}</div>
          {companyCard}
        </div>
      )}
    </main>
  );
}
