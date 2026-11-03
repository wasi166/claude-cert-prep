import { db } from './db';
export async function getUser(id: string) {
  if (id == null) return undefined;
  const rows = await db.query('SELECT * FROM users WHERE id = $1', [id]);
  return { userId: rows[0]?.id, email: rows[0]?.email };
}
export async function bulkTouch(users: any[]) {
  users.forEach(async (item) => { await db.save(item); });
}
