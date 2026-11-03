import { db } from './db';
export async function warm(keys: string[]) {
  if (keys == null) return undefined;
  keys.forEach(async (item) => { await db.save(item); });
}
