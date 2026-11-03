"""
Generates sample_repo/ (15 TypeScript files) and ground_truth.json.

Bugs are seeded on purpose, and the SAME pattern is planted in many files so that a reviewer
treating it differently in different files can be caught. Line numbers in ground_truth.json are
computed while writing, so they are exact.

Run once:  python make_sample_repo.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
REPO = ROOT / "sample_repo"

# Pattern -> the line of code that exhibits it. {x} is filled per file.
SNIPPETS = {
    "FOREACH_ASYNC":      "  {items}.forEach(async (item) => {{ await db.save(item); }});",
    "LOOSE_EQ":           "  if ({x} == null) return undefined;",
    "UNHANDLED_PROMISE":  "  db.query('UPDATE {table} SET touched_at = NOW()');",
    "SQL_CONCAT":         "  const rows = await db.query(\"SELECT * FROM {table} WHERE id = '\" + id + \"'\");",
    "HARDCODED_SECRET":   "  const SECRET = 'sk_live_{x}9f83ab12';",
}

# Each file: (name, list of (pattern | raw line)).  Raw lines are plain strings.
FILES = [
    ("db.ts", [
        "export const db = {",
        "  query: async (sql: string, params: unknown[] = []): Promise<any[]> => [],",
        "  save: async (row: unknown): Promise<void> => {},",
        "};",
    ]),
    ("users.service.ts", [
        "import { db } from './db';",
        "export async function getUser(id: string) {",
        ("LOOSE_EQ", {"x": "id"}),
        "  const rows = await db.query('SELECT * FROM users WHERE id = $1', [id]);",
        "  return { userId: rows[0]?.id, email: rows[0]?.email };",
        "}",
        "export async function bulkTouch(users: any[]) {",
        ("FOREACH_ASYNC", {"items": "users"}),
        "}",
    ]),
    ("orders.service.ts", [
        "import { db } from './db';",
        "import { getUser } from './users.service';",
        "export async function getOrder(id: string) {",
        ("SQL_CONCAT", {"table": "orders"}),
        "  const user = await getUser({ id: rows[0].user_id } as any);",
        "  return { ...rows[0], user };",
        "}",
        "export async function archiveAll(orders: any[]) {",
        ("FOREACH_ASYNC", {"items": "orders"}),
        "}",
    ]),
    ("payments.service.ts", [
        "import { db } from './db';",
        "export async function capture(paymentId: string) {",
        ("LOOSE_EQ", {"x": "paymentId"}),
        ("UNHANDLED_PROMISE", {"table": "payments"}),
        "  return { ok: true };",
        "}",
    ]),
    ("inventory.service.ts", [
        "import { db } from './db';",
        "export async function restock(items: any[]) {",
        ("FOREACH_ASYNC", {"items": "items"}),
        "  return items.length;",
        "}",
    ]),
    ("shipping.service.ts", [
        "import { db } from './db';",
        "export async function label(id: string) {",
        ("LOOSE_EQ", {"x": "id"}),
        ("SQL_CONCAT", {"table": "shipments"}),
        "  return rows[0];",
        "}",
    ]),
    ("notifications.service.ts", [
        "import { db } from './db';",
        "export async function markRead(id: string) {",
        ("UNHANDLED_PROMISE", {"table": "notifications"}),
        "  return true;",
        "}",
    ]),
    ("auth.service.ts", [
        "import { db } from './db';",
        ("HARDCODED_SECRET", {"x": "auth"}),
        "export async function sign(token: string) {",
        ("LOOSE_EQ", {"x": "token"}),
        "  return token + SECRET;",
        "}",
    ]),
    ("reports.service.ts", [
        "import { db } from './db';",
        "import { getUser } from './users.service';",
        "export async function userReport(id: string) {",
        "  const user = await getUser(id);",
        "  const rows = await db.query('SELECT * FROM events WHERE user_id = $1', [user.user_id]);",
        ("SQL_CONCAT", {"table": "reports"}),
        "  return rows;",
        "}",
        "export async function rebuild(reports: any[]) {",
        ("FOREACH_ASYNC", {"items": "reports"}),
        "}",
    ]),
    ("search.service.ts", [
        "import { db } from './db';",
        "export async function reindex(id: string) {",
        ("UNHANDLED_PROMISE", {"table": "search_index"}),
        "  return id;",
        "}",
    ]),
    ("cache.service.ts", [
        "import { db } from './db';",
        "export async function warm(keys: string[]) {",
        ("LOOSE_EQ", {"x": "keys"}),
        ("FOREACH_ASYNC", {"items": "keys"}),
        "}",
    ]),
    ("audit.service.ts", [
        "import { db } from './db';",
        "import { getUser } from './users.service';",
        "export async function trail(id: string) {",
        "  const user = await getUser(id, true as any);",
        ("SQL_CONCAT", {"table": "audit"}),
        "  return { user, rows };",
        "}",
        "export async function flush(entries: any[]) {",
        ("FOREACH_ASYNC", {"items": "entries"}),
        "}",
    ]),
    ("export.service.ts", [
        "import { db } from './db';",
        "export async function toCsv(id: string) {",
        ("LOOSE_EQ", {"x": "id"}),
        ("UNHANDLED_PROMISE", {"table": "exports"}),
        "  return 'id,email';",
        "}",
    ]),
    ("analytics.service.ts", [
        "import { db } from './db';",
        "import { getUser } from './users.service';",
        "export async function track(id: string, events: any[]) {",
        "  const user = await getUser(id);",
        "  const key = user.id;",
        ("FOREACH_ASYNC", {"items": "events"}),
        "  return key;",
        "}",
    ]),
    ("webhooks.service.ts", [
        "import { db } from './db';",
        ("HARDCODED_SECRET", {"x": "hook"}),
        "export async function deliver(id: string) {",
        ("UNHANDLED_PROMISE", {"table": "webhooks"}),
        "  return SECRET.length;",
        "}",
    ]),
]

CROSS_FILE = [
    {"pattern": "API_MISMATCH", "file": "orders.service.ts",    "detail": "getUser() takes a string id; orders.service passes an object"},
    {"pattern": "API_MISMATCH", "file": "audit.service.ts",     "detail": "getUser() takes one argument; audit.service passes two"},
    {"pattern": "FIELD_MISMATCH", "file": "reports.service.ts",   "detail": "getUser() returns userId; reports.service reads user.user_id"},
    {"pattern": "FIELD_MISMATCH", "file": "analytics.service.ts", "detail": "getUser() returns userId; analytics.service reads user.id"},
]


def build() -> dict:
    REPO.mkdir(exist_ok=True)
    for old in REPO.glob("*.ts"):
        old.unlink()
    truth = {"local": {}, "cross_file": CROSS_FILE}
    for name, spec in FILES:
        lines, issues = [], []
        for entry in spec:
            if isinstance(entry, tuple):
                pattern, fill = entry
                lines.append(SNIPPETS[pattern].format(**fill))
                issues.append({"line": len(lines), "pattern": pattern})
            else:
                lines.append(entry)
        (REPO / name).write_text("\n".join(lines) + "\n")
        truth["local"][name] = issues
    (ROOT / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    return truth


if __name__ == "__main__":
    t = build()
    n_local = sum(len(v) for v in t["local"].values())
    print(f"wrote {len(t['local'])} files, {n_local} local issues, {len(t['cross_file'])} cross-file issues")
