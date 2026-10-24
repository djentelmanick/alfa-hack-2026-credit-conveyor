import { http, tokenStore, wsUrl } from "./client.js";

/** Обращения к бэкенду. Пути и поля — из макетов «Credit Cabinet» (поля справа от артбордов). */
export const realApi = {
  // Вход и пользователь
  login: (login, password) => http.post("/auth/login", { login, password }),
  register: (body) => http.post("/auth/register", body),
  me: () => http.get("/auth/me"),

  // Справочники и продукты
  purposes: () => http.get("/dictionaries/purposes"),
  collateralTypes: () => http.get("/dictionaries/collateral-types"),
  products: () => http.get("/products"),
  recommendation: (purpose) => http.get("/products/recommendation", { purpose }),

  // Заявки клиента
  applications: (query) => http.get("/applications", query),
  application: (id) => http.get(`/applications/${id}`),
  createApplication: (body) => http.post("/applications", body),
  updateApplication: (id, body) => http.patch(`/applications/${id}`, body),
  validateApplication: (id) => http.post(`/applications/${id}/validate`),
  submitApplication: (id) => http.post(`/applications/${id}/submit`),
  uploadStatement: (id, file) => http.upload(`/applications/${id}/statements`, file),
  deleteStatement: (id, statementId) => http.del(`/applications/${id}/statements/${statementId}`),
  decision: (id) => http.get(`/applications/${id}/decision`),
  history: (id) => http.get(`/applications/${id}/history`),
  accept: (id, offerId) => http.post(`/applications/${id}/accept`, { offer_id: offerId }),
  cancel: (id) => http.post(`/applications/${id}/cancel`),
  signingCode: (id) => http.post(`/applications/${id}/signing/code`),
  signingConfirm: (id, code) => http.post(`/applications/${id}/signing/confirm`, { code }),

  consents: (inn) => http.get("/consents", { inn }),
  giveConsents: (inn, types) => http.post("/consents", { inn, types }),

  /**
   * Статус заявки в реальном времени: вебсокет /applications/{id}/status → {status, step_label, expires_at}.
   * Переподключается сам. Возвращает функцию отписки.
   */
  subscribeStatus(id, onMessage) {
    let ws;
    let closed = false;
    let delay = 1000;
    const open = () => {
      const token = tokenStore.get();
      ws = new WebSocket(wsUrl(`/applications/${id}/status`) + (token ? `?token=${encodeURIComponent(token)}` : ""));
      ws.onopen = () => { delay = 1000; };
      ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* не JSON — пропускаем */ } };
      ws.onclose = () => {
        if (closed) return;
        setTimeout(open, delay);
        delay = Math.min(delay * 2, 15000);
      };
    };
    open();
    return () => { closed = true; ws && ws.close(); };
  },

  // Договоры
  contracts: () => http.get("/contracts"),
  contract: (id) => http.get(`/contracts/${id}`),
  schedule: (id, version) => http.get(`/contracts/${id}/schedule`, { version }),
  operations: (id) => http.get(`/contracts/${id}/operations`),
  versions: (id) => http.get(`/contracts/${id}/versions`),
  takeTranche: (id, amount) => http.post(`/contracts/${id}/tranches`, { amount }),
  repayTranche: (id, trancheId, amount) => http.post(`/contracts/${id}/tranches/${trancheId}/repay`, { amount }),
  repay: (id, kind, amount) => http.post(`/contracts/${id}/repayments`, { kind, amount }),
  emulateNextMonth: (id) => http.post(`/contracts/${id}/emulation/next-month`),
  emulateRewind: (id, targetDate) => http.post(`/contracts/${id}/emulation/rewind`, { target_date: targetDate }),
  emulateReset: (id) => http.post(`/contracts/${id}/emulation/reset`),

  // Аналитик
  analystApplications: (query) => http.get("/analyst/applications", query),
  analystReport: (id) => http.get(`/analyst/applications/${id}/report`),
  analystApprove: (id, body) => http.post(`/analyst/applications/${id}/approve`, body),
  analystCounterOffer: (id, body) => http.post(`/analyst/applications/${id}/counter-offer`, body),
  analystRequestInfo: (id, body) => http.post(`/analyst/applications/${id}/request-info`, body),
  analystReject: (id, body) => http.post(`/analyst/applications/${id}/reject`, body),
  analystRecheck: (id, body) => http.post(`/analyst/applications/${id}/recheck`, body),
  analystRejectReasons: () => http.get("/dictionaries/reject-reasons"),
  analystUpdateContract: (id, body) => http.patch(`/analyst/contracts/${id}`, body),
};
