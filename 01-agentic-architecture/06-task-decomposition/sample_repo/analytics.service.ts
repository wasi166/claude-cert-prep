import { db } from './db';
import { getUser } from './users.service';
export async function track(id: string, events: any[]) {
  const user = await getUser(id);
  const key = user.id;
  events.forEach(async (item) => { await db.save(item); });
  return key;
}
