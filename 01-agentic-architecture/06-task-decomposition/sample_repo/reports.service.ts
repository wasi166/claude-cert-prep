import { db } from './db';
import { getUser } from './users.service';
export async function userReport(id: string) {
  const user = await getUser(id);
  const rows = await db.query('SELECT * FROM events WHERE user_id = $1', [user.user_id]);
  const rows = await db.query("SELECT * FROM reports WHERE id = '" + id + "'");
  return rows;
}
export async function rebuild(reports: any[]) {
  reports.forEach(async (item) => { await db.save(item); });
}
