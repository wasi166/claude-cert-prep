import { db } from './db';
export async function capture(paymentId: string) {
  if (paymentId == null) return undefined;
  db.query('UPDATE payments SET touched_at = NOW()');
  return { ok: true };
}
