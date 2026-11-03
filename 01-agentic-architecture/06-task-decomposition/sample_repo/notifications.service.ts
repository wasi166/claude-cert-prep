import { db } from './db';
export async function markRead(id: string) {
  db.query('UPDATE notifications SET touched_at = NOW()');
  return true;
}
