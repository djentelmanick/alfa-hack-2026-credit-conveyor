import { http, tokenStore, wsUrl } from "./client.js";

export const realApi = {
  login: (login, password) => http.post("/auth/login", { login, password }),
  register: (body) => http.post("/auth/register", body),
  me: () => http.get("/auth/me"),

  purposes: () => http.get("/dictionaries/purposes"),
  collateralTypes: () => http.get("/dictionaries/collateral-types"),
  products: () => http.get("/products"),
  recommendation: (purpose) => http.post("/products/recommendation", { purpose }),

  company: (inn) => http.post("/companies/get", { inn }),
  consents: (inn) => http.post("/consents/list", { inn }),
  giveConsents: (inn, types) => http.post("/consents/give", { inn, types }),
  claimAuthority: (inn, role, powerOfAttorney) => http.post("/companies/authority", { inn, role, power_of_attorney: powerOfAttorney || null }),

  applications: ({ status, page = 1, size = 100 } = {}) => http.post("/applications/list", { status: status || null, page, size }),
  application: (id) => http.post("/applications/get", { application_id: id }),
  createApplication: (body) => http.post("/applications/create", body),
  updateApplication: (id, body) => http.post("/applications/update", { application_id: id, ...body }),
  validateApplication: (id) => http.post("/applications/validate", { application_id: id }),
  submitApplication: (id) => http.post("/applications/submit", { application_id: id }),
  uploadStatement: (id, file) => http.upload("/applications/statements/upload", file, { application_id: id }),
  deleteStatement: (id, statementId) => http.post("/applications/statements/delete", { application_id: id, statement_id: statementId }),
  statementUrl: (id, statementId) => http.post("/applications/statements/download-url", { application_id: id, statement_id: statementId }),
  decision: (id) => http.post("/applications/decision", { application_id: id }),
  history: (id) => http.post("/applications/history", { application_id: id }),
  accept: (id, offerId) => http.post("/applications/accept", { application_id: id, offer_id: offerId || null }),
  cancel: (id, reason) => http.post("/applications/cancel", { application_id: id, reason: reason || null }),
  signingCode: (id) => http.post("/applications/signing/code", { application_id: id }),
  signingConfirm: (id, code) => http.post("/applications/signing/confirm", { application_id: id, code }),

  subscribeStatus(id, onMessage) {
    let ws;
    let closed = false;
    let open = false;
    let delay = 1000;
    let ping;
    const connect = () => {
      const token = tokenStore.get();
      if (!token) return;
      ws = new WebSocket(wsUrl("/ws/applications"), ["bearer", token]);
      ws.onopen = () => {
        open = true;
        delay = 1000;
        ping = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "ping" })), 25000);
      };
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        if (!msg || msg.type === "pong") return;
        if (msg.type === "snapshot") {
          if (msg.applications?.some((a) => String(a.application_id) === String(id))) onMessage(msg);
          return;
        }
        if (msg.application_id && String(msg.application_id) !== String(id)) return;
        onMessage(msg);
      };
      ws.onclose = (e) => {
        open = false;
        clearInterval(ping);
        if (closed || e.code === 4401) return;
        setTimeout(() => { if (!closed) connect(); }, delay);
        delay = Math.min(delay * 2, 15000);
      };
    };
    connect();
    const poll = setInterval(() => { if (!open) onMessage({ type: "poll" }); }, 3000);
    return () => { closed = true; clearInterval(poll); clearInterval(ping); ws && ws.close(); };
  },

  contracts: (applicationId) => http.post("/contracts/list", { application_id: applicationId || null }),
  contract: (id) => http.post("/contracts/get", { contract_id: id }),
  schedule: (id, versionNo) => http.post("/contracts/schedule", { contract_id: id, version_no: versionNo || null }),
  operations: (id, { page = 1, size = 100 } = {}) => http.post("/contracts/operations", { contract_id: id, page, size }),
  versions: (id) => http.post("/contracts/versions", { contract_id: id }),
  documentUrl: (id, versionNo) => http.post("/contracts/document-url", { contract_id: id, version_no: versionNo || null }),
  takeTranche: (id, amount) => http.post("/contracts/tranches/draw", { contract_id: id, amount }),
  repayTranche: (id, trancheId, amount) => http.post("/contracts/tranches/repay", { contract_id: id, tranche_id: trancheId, amount }),
  repay: (id, kind, amount) => http.post("/contracts/repay", { contract_id: id, kind, amount: kind === "PARTIAL" ? amount : null }),
  emulateNextMonth: (id) => http.post("/contracts/emulation/next-month", { contract_id: id }),
  emulateRewind: (id, targetDate) => http.post("/contracts/emulation/rewind", { contract_id: id, target_date: targetDate }),
  emulateReset: (id) => http.post("/contracts/emulation/reset", { contract_id: id }),

  analystApplications: ({ queue, status, inn, risk_class, page = 1, size = 20 } = {}) =>
    http.post("/analyst/applications/list", { queue: queue || null, status: status || null, inn: inn || null, risk_class: risk_class || null, page, size }),
  analystReport: (id) => http.post("/analyst/applications/report", { application_id: id }),
  analystApprove: (id, body) => http.post("/analyst/applications/approve", { application_id: id, ...body }),
  analystCounterOffer: (id, body) => http.post("/analyst/applications/counter-offer", { application_id: id, ...body }),
  analystRequestInfo: (id, body) => http.post("/analyst/applications/request-info", { application_id: id, ...body }),
  analystReject: (id, body) => http.post("/analyst/applications/reject", { application_id: id, ...body }),
  analystRecheck: (id, body) => http.post("/analyst/applications/recheck", { application_id: id, ...body }),
  analystUpdateContract: (id, body) => http.post("/analyst/contracts/update", { contract_id: id, ...body }),
};
