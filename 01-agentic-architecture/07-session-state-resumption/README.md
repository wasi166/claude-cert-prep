# 1.7 — Session State and Resumption

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** resume vs fork vs fresh start, stale context, structured summary injection, targeted re-analysis

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- The three session management options: resume, `fork_session`, and fresh start with summary injection
- Why resuming after file changes leads to the stale context problem
- How structured summary injection preserves knowledge without stale tool results
- When targeted re-analysis is more efficient than full re-exploration
- The difference between `fork_session` (divergent exploration) and resume (continuation)

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | A Claude Code session that analyses a **10-file codebase**, named for later resumption. | Named sessions enable continuation across breaks. The exam tests when resume is appropriate (nothing changed) vs when it creates stale context (files modified). | A named session that reads 10 files and reports findings per file. | `make_workspace()`, step 1 in `main()` |
| 2 | Record findings as a **structured summary** (file, issues, severity, recommendations). | This is what you inject into the fresh session. Preserve conclusions, not raw tool output. | A concise structured document, complete enough to carry every key finding. | `build_summary()`, `summary.json` |
| 3 | **Modify 3 files** to fix identified issues. | Creates the stale-context condition: old contents remain in session history as tool results; disk now differs. | Three files substantively changed so old and new would review differently. | `FIXES`, `apply_fixes()` |
| 4 | **Resume** the session and observe stale context. | The resumed session still holds the old Read results. Expect advice to fix already-fixed issues, or contradictions. | Contradictory or outdated advice about the modified files. | step 4 in `main()`, `base_options(resume=…)` |
| 5 | **Fresh session** with the summary injected, naming the 3 changed files for targeted re-analysis. | The correct approach after changes: no stale tool results, prior knowledge preserved, only changed files re-read. | A clean session that knows the prior findings, reads only 3 files, gives consistent advice. | step 5 in `main()`, `fresh_prompt` |
| 6 | **Compare** quality and consistency between stale resume and fresh start. | Shows why the exam favours fresh+summary over naive resume after edits. | Resume: contradictions. Fresh: accurate, consistent. | `stale_claims()`, `missed_claims()`, the comparison table |

## What this builds

```
 session 1 ── analyse 10 files ──► findings ──► summary.json
                                      │
                 fix 3 files ─────────┤
                                      │
        ┌─────────────────────────────┴───────────────────────────┐
        ▼                                                         ▼
 RESUME session 1                                      FRESH + summary.json
 old Read results still in context                     "these 3 changed; re-read only those"
        │                                                         │
        └── score: stale claims · missed issues · files re-read · input tokens ──┘

 FORK session 1 ──► new session id; original untouched
```

The scoring doesn't depend on reading the model's prose for "contradictions". After the fixes,
`issues_present_now(file)` regex-checks the **current** file on disk. A review that says
`orders.ts` still has `SQL_CONCAT` when the file no longer contains it is a **stale claim** —
counted, not interpreted.

Expected output:

```
STEP 1 — session 1: analyse all 10 files
  [session 1] session=a81c2f0e  reads=10 [...]  input_tokens=18420
STEP 2 — structured summary written to summary.json
  users.ts       ['LOOSE_EQ']  medium
  orders.ts      ['SQL_CONCAT']  high
  ...
STEP 3 — fixing ['auth.ts', 'inventory.ts', 'orders.ts'] on disk
  auth.ts        issues now: none
  ...
STEP 4 — RESUME session 1 after the edits
  [resume] session=a81c2f0e  reads=0 []  input_tokens=21930
STEP 5 — FRESH session with summary injected, targeted re-analysis of the 3 changed files
  [fresh+summary] session=5d0e77b1  reads=3 ['auth.ts', 'inventory.ts', 'orders.ts']  input_tokens=6100
FORK — branch a new session off session 1 (original untouched)
  original session a81c2f0e  ->  fork got NEW id c93b1a44

STEP 6 — comparison
                                                          RESUME    FRESH
  stale claims (issue 'still present' that was fixed)          3        0
  missed issues in changed files                               0        0
  files re-read                                                0        3
  input tokens                                             21930     6100
```

Numbers vary per run. Two outcomes are possible for the resume, and both teach the lesson:
either it answers from the old Read results (stale claims > 0), or it re-reads everything
(reads = 10, tokens high) — paying full re-exploration cost *and* still carrying the old results
in context. The fresh session does neither.

## Files

| File | Purpose |
|---|---|
| `sessions.py` | **The lesson.** Workspace generator, fixes, the three session strategies, scoring, comparison. |
| `test_sessions.py` | Offline tests: workspace and fixes; ground truth moves with the files; stale/missed detection; summary shape; resume/fork/fresh options. |
| `workspace/`, `summary.json` | Generated at runtime (gitignored). |

## How to run

```bash
pip install claude-agent-sdk
cd 01-agentic-architecture/07-session-state-resumption
python sessions.py       # 4 sessions: analyse, resume, fresh, fork
pytest -q
```

### CLI equivalents

The SDK's `resume=` and `fork_session=` are the same mechanisms as the CLI flags:

| Intent | CLI | SDK |
|---|---|---|
| name a session for later | `claude --name codebase-review` | (`session_id` from `ResultMessage`) |
| continue it | `claude --resume codebase-review` | `ClaudeAgentOptions(resume=session_id)` |
| branch off it | `claude --resume codebase-review --fork-session` | `ClaudeAgentOptions(resume=session_id, fork_session=True)` |
| fresh with prior knowledge | `claude "Prior findings: … Only X, Y, Z changed …"` | new `query()` with the summary in the prompt |

## Code walkthrough

### Step 1 — Session 1 and its id

```python
s1 = await run("Analyse every .ts file ... Read each one. " + REVIEW_FORMAT, base_options(), "session 1")
session_id = s1["session_id"]          # from ResultMessage.session_id
```

The session's full history — including the raw content of every `Read` — is stored under that
id. That's what makes resume possible, and what makes it dangerous later.

### Step 2 — The summary carries conclusions, not output

```python
def build_summary(review):
    return {"scope": ..., "files": {r["file"]: {"issues": ..., "severity": ..., "recommendation": ...}}}
```

Three fields per file. No source code, no tool results. `test_summary_is_conclusions_not_tool_output`
asserts `"db.query"` doesn't appear anywhere in it. This is the difference between "what we
learned" and "what we saw" — the fresh session needs the first and must not get the second.

### Step 3 — Fixes that change the verdict

```python
FIXES = {
    "orders.ts":    (SQL string concat,        parameterised query),
    "inventory.ts": (forEach(async …),         for…of with await),
    "auth.ts":      (hard-coded secret,        process.env.SIGNING_SECRET),
}
```

Each fix removes the file's only seeded issue, so `issues_present_now()` goes from one pattern
to none. Old and new content would produce opposite reviews — which is what makes stale advice
detectable.

### Step 4 — Resume: the stale context problem

```python
r = await run(question, base_options(resume=session_id), "resume")
```

Session 1's history comes back verbatim: the model's own analysis *and* the old `Read` results
showing `+ id +` in `orders.ts`. The prompt says files have changed. The model now holds two
sources of truth. Three things can happen:

1. It answers from memory → stale claims (recommends fixing what's fixed).
2. It re-reads everything → correct, but paid for 10 reads and is still carrying 10 old ones.
3. It re-reads some → mixed; consistent for those, stale for the rest.

None of these is good. The old tool results can't be removed from a resumed session.

### Step 5 — Fresh + summary + targeted re-analysis

```python
fresh_prompt = ("Prior analysis ... (conclusions only):\n" + json.dumps(summary)
                + f"\n\nSince then, exactly these files changed: {CHANGED}. Re-read ONLY those three ..."
                + "For all other files, carry the prior findings forward unchanged — do not re-read them.")
```

Three ingredients, each doing one job: the **summary** preserves the seven unchanged files'
findings for free; the **changed list** scopes the work; the **"do not re-read"** instruction
stops full re-exploration. Expect `reads=3` and a fraction of the tokens.

### Fork — divergent, not continuation

```python
k = await run("Which single file would you fix first?", base_options(resume=session_id, fork_session=True), "fork")
```

`fork_session=True` with `resume=` creates a **new** session id seeded with the old history.
Session 1 is untouched — you can fork it again, or resume it, and neither sees the fork's turns.
Use it to explore a "what if" without polluting the main line. It does *not* solve stale
context: the fork inherits the same old tool results.

### Step 6 — Scoring

```python
def stale_claims(review):   # says issue X in file F, but F's current content has no X
def missed_claims(review):  # F's current content has X, review of F doesn't list it (changed files only)
```

Both are computed against the files on disk at scoring time. That's the only honest reference:
not the generator's intent, not the model's earlier analysis.

## Key takeaways

1. **Resume = same session, full history, including old tool results.** Right when nothing on disk changed since.
2. **Stale context = old tool results contradicting current reality.** It can't be purged from a resumed session; the model holds both.
3. **Fresh + summary = knowledge without baggage.** Carry forward conclusions (file, issues, severity, recommendation), never raw output.
4. **Targeted re-analysis**: name exactly what changed, say "re-read only those", carry the rest forward. Reads drop from N to k.
5. **Fork = branch for divergent exploration.** New id, original untouched. Same stale-context exposure as resume.
6. **Decide by one question: did the world change?** No → resume. Yes → fresh with summary. Want to try something without committing → fork.
7. **Measure staleness against disk, not against prose.** A claim is stale if the file doesn't contain what it describes today.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| `--resume` after editing files | Old `Read` results remain in context; advice for code that no longer exists. |
| Telling the resumed session "files changed, please re-check" | It may re-read everything (full cost) and the old results are still there. Doesn't remove the contradiction. |
| Fresh session with no summary | Loses everything learned; full re-exploration of all files. |
| Fresh session with the old transcript pasted in | That's resume by another name — the stale tool output comes along. |
| Summary that includes file contents or raw tool output | Injects the staleness you were avoiding. Conclusions only. |
| Fresh session told "re-analyse the codebase" without naming the changed files | Re-reads all N files. Targeted means the changed list is in the prompt. |
| Using `fork_session` to escape stale context | The fork inherits the old history. It's for divergence, not freshness. |
| Judging staleness by whether the advice "sounds contradictory" | Score against the current file content. |

## Strategy reference

| | Resume | Fork | Fresh + summary |
|---|---|---|---|
| Session id | same | new | new |
| Carries | full history incl. tool results | full history incl. tool results | only what you inject |
| Original session afterwards | advanced | unchanged | unchanged |
| Safe after file changes? | no | no | yes |
| Cost to continue | low (if nothing changed) | low | summary + k targeted reads |
| Use for | continuation, nothing changed | "what if" exploration | continuation after the world changed |
