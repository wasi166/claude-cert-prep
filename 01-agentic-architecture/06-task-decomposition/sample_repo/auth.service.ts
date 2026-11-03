import { db } from './db';
  const SECRET = 'sk_live_auth9f83ab12';
export async function sign(token: string) {
  if (token == null) return undefined;
  return token + SECRET;
}
