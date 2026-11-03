import { db } from './db';
import { getUser } from './users.service';
export async function trail(id: string) {
  const user = await getUser(id, true as any);
  const rows = await db.query("SELECT * FROM audit WHERE id = '" + id + "'");
  return { user, rows };
}
export async function flush(entries: any[]) {
  entries.forEach(async (item) => { await db.save(item); });
}
