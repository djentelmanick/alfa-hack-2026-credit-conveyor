const NBSP = " ";
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const MONTHS_SHORT = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."];
const MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

export function formatMoney(value, currency = true) {
  const n = Number(value) || 0;
  const cents = Math.round(Math.abs(n) * 100);
  const int = String(Math.floor(cents / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const frac = cents % 100 ? "," + String(cents % 100).padStart(2, "0") : "";
  return (n < 0 ? "−" : "") + int + frac + (currency ? NBSP + "₽" : "");
}

export const formatNumber = (n) => formatMoney(n, false);

export const formatRate = (r) => String(r).replace(".", ",") + "%";

const toDate = (v) => (v instanceof Date ? v : new Date(v));

export function formatDate(v) {
  if (!v) return "";
  const d = toDate(v);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]} ${d.getFullYear()}`;
}

export function formatDayMonth(v) {
  if (!v) return "";
  const d = toDate(v);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
}

export function formatDateTime(v) {
  if (!v) return "";
  const d = toDate(v);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}, ${hh}:${mm}`;
}

export function formatTime(v) {
  const d = toDate(v);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function formatDateNumeric(v) {
  const d = toDate(v);
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
}

export function parseDateNumeric(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(String(s).trim());
  if (!m) return null;
  const iso = `${m[3]}-${m[2]}-${m[1]}`;
  return Number.isNaN(new Date(iso).getTime()) ? null : iso;
}

export function formatPeriod(from, to) {
  const a = toDate(from);
  const b = toDate(to);
  if (a.getFullYear() === b.getFullYear()) return `${MONTHS_NOM[a.getMonth()]} — ${MONTHS_NOM[b.getMonth()]} ${b.getFullYear()}`;
  return `${MONTHS_NOM[a.getMonth()]} ${a.getFullYear()} — ${MONTHS_NOM[b.getMonth()]} ${b.getFullYear()}`;
}

export function plural(n, one, few, many) {
  const m = n % 100;
  const d = n % 10;
  if (m > 10 && m < 20) return many;
  if (d === 1) return one;
  if (d > 1 && d < 5) return few;
  return many;
}

export const firstName = (fullName = "") => fullName.trim().split(/\s+/)[0] || "";
