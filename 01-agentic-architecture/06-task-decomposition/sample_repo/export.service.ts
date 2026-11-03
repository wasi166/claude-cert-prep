import { db } from './db';
export async function toCsv(id: string) {
  if (id == null) return undefined;
  db.query('UPDATE exports SET touched_at = NOW()');
  return 'id,email';
}
