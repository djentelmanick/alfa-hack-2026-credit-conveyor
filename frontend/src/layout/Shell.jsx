import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { AppHeader, NetworkBanner, Toast } from "../components/index.js";
import { onNetworkChange, retryAll } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useIsMobile } from "../lib/hooks.js";
import { firstName } from "../lib/format.js";

const ToastContext = createContext(() => {});
/** Тост — только после подписания договора и решения аналитика. */
export const useToast = () => useContext(ToastContext);

function Frame({ header, children }) {
  const [down, setDown] = useState(false);
  const [toast, setToast] = useState(null);
  useEffect(() => onNetworkChange(setDown), []);
  const show = useCallback((message) => setToast({ message, key: Date.now() }), []);
  return (
    <ToastContext.Provider value={show}>
      {header}
      {down && <div className="page__banner"><NetworkBanner onRetry={retryAll} /></div>}
      {children}
      <Toast key={toast?.key} message={toast?.message} open={!!toast} onClose={() => setToast(null)} />
    </ToastContext.Provider>
  );
}

export function ClientLayout() {
  const { user, company, selectCompany, logout } = useAuth();
  const companies = user.companies || [];
  const mobile = useIsMobile();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const onContracts = pathname.startsWith("/contracts");
  const exit = () => { logout(); navigate("/login"); };
  const links = [
    { label: "Мои заявки", to: "/", current: !onContracts },
    { label: "Договоры", to: "/contracts", current: onContracts },
  ];
  const header = (
    <AppHeader
      title={company ? company.name : user.full_name}
      links={mobile ? [] : links}
      userName={mobile ? firstName(user.full_name) : user.full_name}
      menuItems={mobile ? [...links.map((l) => ({ label: l.label, onClick: () => navigate(l.to) })), { label: "Выйти", onClick: exit }] : [{ label: "Выйти", onClick: exit }]}
      companies={companies.length > 1 ? companies.map((c) => ({ label: c.name, onClick: () => { selectCompany(c.inn); navigate("/"); } })) : null}
    />
  );
  return <Frame header={header}><Outlet /></Frame>;
}

export function AnalystLayout() {
  const { pathname } = useLocation();
  const onContracts = pathname.startsWith("/analyst/contracts");
  const header = (
    <AppHeader
      title="Кредитный аналитик"
      homeTo="/analyst"
      links={[
        { label: "Заявки", to: "/analyst", current: !onContracts },
        { label: "Договоры", to: "/analyst/contracts", current: onContracts },
        { label: "Выйти", to: "/logout" },
      ]}
    />
  );
  return <Frame header={header}><Outlet /></Frame>;
}
