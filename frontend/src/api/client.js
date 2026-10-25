export const API_URL = (import.meta.env.VITE_API_URL || "/api/v1").replace(/\/$/, "");
export const WS_URL = (import.meta.env.VITE_WS_URL || "").replace(/\/$/, "");

const TOKEN_KEY = "ck.token";

export const tokenStore = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch {  } },
};

export class ApiError extends Error {
  constructor(status, data) {
    const body = (data && data.error) || data || {};
    super(body.message || (typeof body.detail === "string" && body.detail) || `HTTP ${status}`);
    this.status = status;
    this.code = body.code || null;
    this.details = Array.isArray(body.details) ? body.details : [];
    this.data = data || {};
  }
}

export function fieldErrors(err) {
  const list = err?.details?.length ? err.details : err?.data?.detail;
  if (!Array.isArray(list)) return {};
  const key = (e) => String(e.field || (Array.isArray(e.loc) ? e.loc.slice(1).join(".") : "")).replace(/^body\./, "").replace(/\.(\d+)(?=\.|$)/g, "[$1]");
  return Object.fromEntries(list.map((e) => [key(e), e.message || e.msg]));
}

export function errorText(err) {
  return err?.details?.[0]?.message || err?.message || "Не получилось";
}

const netListeners = new Set();
const retryListeners = new Set();
export const onNetworkChange = (fn) => { netListeners.add(fn); return () => netListeners.delete(fn); };
export const setNetworkDown = (down) => netListeners.forEach((fn) => fn(down));
export const onRetry = (fn) => { retryListeners.add(fn); return () => retryListeners.delete(fn); };
export const retryAll = () => { setNetworkDown(false); retryListeners.forEach((fn) => fn()); };

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function request(method, path, { body, query, form } = {}) {
  const url = new URL(API_URL + path, window.location.origin);
  if (query) Object.entries(query).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v); });
  const headers = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, { method, headers, body: payload });
  } catch (e) {
    setNetworkDown(true);
    throw new ApiError(0, { message: "Нет связи с сервером" });
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (res.status >= 500) setNetworkDown(true);
  if (res.status === 401 && path !== "/auth/login") onUnauthorized();
  if (res.status === 204) return null;
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const http = {
  get: (p, query) => request("GET", p, { query }),
  post: (p, body) => request("POST", p, { body }),
  upload: (p, file, fields = {}) => {
    const form = new FormData();
    Object.entries(fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", file);
    return request("POST", p, { form });
  },
};

export function wsUrl(path) {
  const base = new URL(API_URL, window.location.origin);
  if (WS_URL) return WS_URL + base.pathname.replace(/\/$/, "") + path;
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return base.toString().replace(/\/$/, "") + path;
}
