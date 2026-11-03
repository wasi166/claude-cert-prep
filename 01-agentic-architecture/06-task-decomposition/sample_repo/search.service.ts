import { db } from './db';
export async function reindex(id: string) {
  db.query('UPDATE search_index SET touched_at = NOW()');
  return id;
}
