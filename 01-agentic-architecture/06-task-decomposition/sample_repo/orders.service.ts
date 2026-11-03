import { db } from './db';
import { getUser } from './users.service';
export async function getOrder(id: string) {
  const rows = await db.query("SELECT * FROM orders WHERE id = '" + id + "'");
  const user = await getUser({ id: rows[0].user_id } as any);
  return { ...rows[0], user };
}
export async function archiveAll(orders: any[]) {
  orders.forEach(async (item) => { await db.save(item); });
}
