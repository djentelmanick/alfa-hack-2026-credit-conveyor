import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/index.js";
import { setUnauthorizedHandler, tokenStore } from "../api/client.js";

const AuthContext = createContext(null);
const COMPANY_KEY = "ck.company";

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [companyInn, setCompanyInn] = useState(() => { try { return localStorage.getItem(COMPANY_KEY); } catch { return null; } });

  const loadMe = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
      return me;
    } catch {
      tokenStore.set(null);
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      if (tokenStore.get()) await loadMe();
      setLoading(false);
    })();
  }, [loadMe]);

  useEffect(() => {
    setUnauthorizedHandler(() => { tokenStore.set(null); setUser(null); });
  }, []);

  const login = useCallback(async (login, password) => {
    const res = await api.login(login, password);
    tokenStore.set(res.access_token);
    return loadMe();
  }, [loadMe]);

  const logout = useCallback(() => {
    tokenStore.set(null);
    setUser(null);
  }, []);

  const selectCompany = useCallback((inn) => {
    setCompanyInn(inn);
    try { localStorage.setItem(COMPANY_KEY, inn); } catch { /* приватный режим */ }
  }, []);

  const company = useMemo(() => {
    const list = user?.companies || [];
    return list.find((c) => c.inn === companyInn) || list[0] || null;
  }, [user, companyInn]);

  const value = useMemo(() => ({ user, loading, login, logout, company, selectCompany, reloadMe: loadMe }), [user, loading, login, logout, company, selectCompany, loadMe]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
