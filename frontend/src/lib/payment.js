/**
 * Ориентировочный аннуитетный платёж для подсказки в анкете и на экране одобрения.
 * Решение и график считает бэкенд, здесь только оценка для клиента. Округляем до сотен, как в макетах.
 */
export function annuityPayment(amount, ratePercent, months) {
  const p = Number(amount);
  const n = Number(months);
  if (!p || !n) return 0;
  const i = Number(ratePercent) / 100 / 12;
  const raw = i ? (p * i) / (1 - Math.pow(1 + i, -n)) : p / n;
  return Math.round(raw / 100) * 100;
}
