import { db } from './db';
export async function label(id: string) {
  if (id == null) return undefined;
  const rows = await db.query("SELECT * FROM shipments WHERE id = '" + id + "'");
  return rows[0];
}
