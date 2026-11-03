"""
Lesson 1.7 — Session State and Resumption

Three ways to continue work after a break:

  RESUME         continue the same session; full history (incl. old tool results) comes back
  FORK           branch a NEW session off the old history; original untouched (divergent exploration)
  FRESH+SUMMARY  new session; inject a structured summary of what was learned; re-read only what changed

The trap: RESUME after files have changed. The old Read results are still in context, showing
code that no longer exists. The model now holds two truths and may give advice for either.

    session 1 ──analyse 10 files──► findings ──► summary.json
                                       │
                  fix 3 files ─────────┤
                                       │
         ┌─────────────────────────────┴──────────────────────────┐
         ▼                                                        ▼
  RESUME session 1                                     FRESH session + summary.json
  (old Read results still in context)                  ("these 3 files changed; re-read only those")
         │                                                        │
         └────────── score: stale claims, files re-read, tokens ──┘

Run:  python sessions.py          (needs claude-agent-sdk and ANTHROPIC_API_KEY in the repo-root .env)
CLI equivalents:  claude --name codebase-review   /   claude --resume codebase-review   /   claude --fork-session
"""

import asyncio
import json
import os
import re
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, ToolUseBlock, query
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
HERE = Path(__file__).parent
WORKSPACE = HERE / "workspace"
SUMMARY_FILE = HERE / "summary.json"

# ===========================================================================
# A 10-file codebase with seeded issues, and the fixes we will apply to 3 of them.
# ===========================================================================
PATTERN_RE = {
    "LOOSE_EQ":      re.compile(r"[^=!]==\s*null"),
    "FOREACH_ASYNC": re.compile(r"\.forEach\(async"),
    "SQL_CONCAT":    re.compile(r"\+ id \+"),
    "HARDCODED_SECRET": re.compile(r"sk_live_"),
}

FILES = {
    "users.ts":    ("LOOSE_EQ",         "export async function getUser(id: string) {\n  if (id == null) return undefined;\n  return db.query('SELECT * FROM users WHERE id = $1', [id]);\n}\n"),
    "orders.ts":   ("SQL_CONCAT",       "export async function getOrder(id: string) {\n  return db.query(\"SELECT * FROM orders WHERE id = '\" + id + \"'\");\n}\n"),
    "inventory.ts": ("FOREACH_ASYNC",   "export async function restock(items: any[]) {\n  items.forEach(async (item) => { await db.save(item); });\n}\n"),
    "payments.ts": ("LOOSE_EQ",         "export async function capture(id: string) {\n  if (id == null) return false;\n  return true;\n}\n"),
    "auth.ts":     ("HARDCODED_SECRET", "const SECRET = 'sk_live_9f83ab12';\nexport function sign(t: string) { return t + SECRET; }\n"),
    "reports.ts":  ("SQL_CONCAT",       "export async function report(id: string) {\n  return db.query(\"SELECT * FROM reports WHERE id = '\" + id + \"'\");\n}\n"),
    "cache.ts":    ("FOREACH_ASYNC",    "export async function warm(keys: string[]) {\n  keys.forEach(async (item) => { await db.save(item); });\n}\n"),
    "search.ts":   ("LOOSE_EQ",         "export async function find(q: string) {\n  if (q == null) return [];\n  return db.query('SELECT * FROM idx WHERE q = $1', [q]);\n}\n"),
    "audit.ts":    ("FOREACH_ASYNC",    "export async function flush(entries: any[]) {\n  entries.forEach(async (item) => { await db.save(item); });\n}\n"),
    "webhooks.ts": ("HARDCODED_SECRET", "const SECRET = 'sk_live_77aa01cd';\nexport function deliver(id: string) { return SECRET.length + id.length; }\n"),
}
HEADER = "import { db } from './db';\n"

FIXES = {   # file -> (old, new). Substantive: a re-read gives a different verdict.
    "orders.ts":    ("db.query(\"SELECT * FROM orders WHERE id = '\" + id + \"'\")",
                     "db.query('SELECT * FROM orders WHERE id = $1', [id])"),
    "inventory.ts": ("items.forEach(async (item) => { await db.save(item); });",
                     "for (const item of items) { await db.save(item); }"),
    "auth.ts":      ("const SECRET = 'sk_live_9f83ab12';",
                     "const SECRET = process.env.SIGNING_SECRET!;"),
}
CHANGED = sorted(FIXES)


def make_workspace():
    WORKSPACE.mkdir(exist_ok=True)
    for old in WORKSPACE.glob("*.ts"):
        old.unlink()
    (WORKSPACE / "db.ts").write_text("export const db = { query: async (s: string, p: unknown[] = []) => [], save: async (r: unknown) => {} };\n")
    for name, (_, body) in FILES.items():
        (WORKSPACE / name).write_text(HEADER + body)


def apply_fixes():
    """Step 3: change 3 files on disk. The old contents live on in session 1's history."""
    for name, (old, new) in FIXES.items():
        p = WORKSPACE / name
        src = p.read_text()
        assert old in src, f"{name}: expected text not found"
        p.write_text(src.replace(old, new))


def issues_present_now(name: str) -> set[str]:
    """Ground truth from the CURRENT file content — what a correct reviewer would say today."""
    src = (WORKSPACE / name).read_text()
    return {p for p, rx in PATTERN_RE.items() if rx.search(src)}


# ===========================================================================
# Driving sessions through the SDK
# ===========================================================================
REVIEW_FORMAT = (
    "Respond with ONLY a JSON array, one item per file: "
    '{"file": "<name>", "issues": ["<LOOSE_EQ|FOREACH_ASYNC|SQL_CONCAT|HARDCODED_SECRET>", ...], '
    '"severity": "high|medium|low", "recommendation": "<one sentence>"}. '
    "An empty issues list means the file is clean."
)


def base_options(**extra) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(WORKSPACE),
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        max_turns=30,
        **extra,
    )


async def run(prompt: str, options: ClaudeAgentOptions, label: str) -> dict:
    """Returns {"text", "session_id", "reads", "input_tokens"}."""
    reads, text, sid, tokens = [], "", None, 0
    async for m in query(prompt=prompt, options=options):
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, ToolUseBlock) and b.name == "Read":
                    reads.append(Path(b.input.get("file_path", "?")).name)
        elif isinstance(m, ResultMessage):
            text, sid = m.result or "", m.session_id
            tokens = (m.usage or {}).get("input_tokens", 0)
    print(f"  [{label}] session={sid[:8] if sid else '?'}  reads={len(reads)} {sorted(set(reads))}  input_tokens={tokens}")
    return {"text": text, "session_id": sid, "reads": reads, "input_tokens": tokens}


def parse_review(text: str) -> list[dict]:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = re.search(r"\[.*\]", text, flags=re.S)
    try:
        return json.loads(m.group(0) if m else text)
    except json.JSONDecodeError:
        return []


# ===========================================================================
# Step 2: the structured summary — conclusions, not tool output
# ===========================================================================
def build_summary(review: list[dict]) -> dict:
    return {
        "scope": "initial analysis of workspace/*.ts",
        "files": {
            r["file"]: {"issues": r.get("issues", []), "severity": r.get("severity", "?"),
                        "recommendation": r.get("recommendation", "")}
            for r in review if "file" in r
        },
    }


# ===========================================================================
# Step 6: scoring
# ===========================================================================
def stale_claims(review: list[dict]) -> list[str]:
    """Claims about an issue that is NOT in the file's current content."""
    out = []
    for r in review:
        name = r.get("file", "")
        if name not in FILES:
            continue
        now = issues_present_now(name)
        for issue in r.get("issues", []):
            if issue in PATTERN_RE and issue not in now:
                out.append(f"{name}: says {issue} still present — it was fixed")
    return out


def missed_claims(review: list[dict]) -> list[str]:
    """Issues present in the current content that the review failed to list (changed files only)."""
    out = []
    by_file = {r.get("file"): set(r.get("issues", [])) for r in review}
    for name in CHANGED:
        for issue in issues_present_now(name) - by_file.get(name, set()):
            out.append(f"{name}: misses {issue}")
    return out


# ===========================================================================
async def main():
    make_workspace()

    print("STEP 1 — session 1: analyse all 10 files")
    s1 = await run(
        "Analyse every .ts file in this directory except db.ts. Read each one. " + REVIEW_FORMAT,
        base_options(), "session 1",
    )
    review1 = parse_review(s1["text"])
    session_id = s1["session_id"]

    print("\nSTEP 2 — structured summary written to summary.json")
    summary = build_summary(review1)
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2))
    for f, v in summary["files"].items():
        print(f"  {f:14s} {v['issues']}  {v['severity']}")

    print(f"\nSTEP 3 — fixing {CHANGED} on disk")
    apply_fixes()
    for f in CHANGED:
        print(f"  {f:14s} issues now: {sorted(issues_present_now(f)) or 'none'}")

    question = ("Files have been edited since your analysis. Give the CURRENT state of every file you "
                "analysed: which issues remain in each. " + REVIEW_FORMAT)

    print("\nSTEP 4 — RESUME session 1 after the edits")
    r = await run(question, base_options(resume=session_id), "resume")
    review_resume = parse_review(r["text"])

    print("\nSTEP 5 — FRESH session with summary injected, targeted re-analysis of the 3 changed files")
    fresh_prompt = (
        "Prior analysis of this codebase (conclusions only, from an earlier session):\n"
        + json.dumps(summary, indent=1)
        + f"\n\nSince then, exactly these files changed: {CHANGED}. Re-read ONLY those three files and "
        "update their entries. For all other files, carry the prior findings forward unchanged — do not "
        "re-read them. " + REVIEW_FORMAT
    )
    f = await run(fresh_prompt, base_options(), "fresh+summary")
    review_fresh = parse_review(f["text"])

    print("\nFORK — branch a new session off session 1 (original untouched)")
    k = await run("In one sentence: which single file would you fix first and why?",
                  base_options(resume=session_id, fork_session=True), "fork")
    print(f"  original session {session_id[:8]}  ->  fork got NEW id {k['session_id'][:8] if k['session_id'] else '?'}")

    print("\nSTEP 6 — comparison")
    rows = [("stale claims (issue 'still present' that was fixed)", len(stale_claims(review_resume)), len(stale_claims(review_fresh))),
            ("missed issues in changed files", len(missed_claims(review_resume)), len(missed_claims(review_fresh))),
            ("files re-read", len(set(r["reads"])), len(set(f["reads"]))),
            ("input tokens", r["input_tokens"], f["input_tokens"])]
    print(f"  {'':55s} {'RESUME':>8s} {'FRESH':>8s}")
    for label, a, b in rows:
        print(f"  {label:55s} {a:>8} {b:>8}")
    for s in stale_claims(review_resume):
        print(f"  resume: {s}")
    for s in stale_claims(review_fresh):
        print(f"  fresh : {s}")


if __name__ == "__main__":
    asyncio.run(main())
