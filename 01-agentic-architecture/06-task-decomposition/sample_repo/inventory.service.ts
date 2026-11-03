import { db } from './db';
export async function restock(items: any[]) {
  items.forEach(async (item) => { await db.save(item); });
  return items.length;
}
