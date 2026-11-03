# 1.6 — Task Decomposition Strategies

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** attention dilution, single-pass vs multi-pass, per-item + cross-item architecture, fixed vs adaptive decomposition, dilution artefacts

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- Why attention dilution produces inconsistent analysis depth across files in single-pass reviews
- How multi-pass architecture (per-item + cross-item) solves the structural attention allocation problem
- The difference between fixed sequential pipelines and dynamic adaptive decomposition
- Why batching without a cross-file integration pass still misses cross-cutting issues
- How to identify attention dilution artefacts: same pattern flagged in one file, approved in another

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | A code review agent that accepts a directory with **≥10 source files**. | 10+ is where dilution becomes observable. The exam's example is 14 files. | A function that reads all files and prepares them for analysis. | `make_sample_repo.py` (15 files), `load_repo()` |
| 2 | A **single-pass review** processing all files at once; record results. | The baseline that shows dilution: thorough early, shallow late, contradictory pattern verdicts. | Early files get detailed feedback with line refs; later files get brief or missing feedback. | `single_pass()` |
| 3 | **Per-file passes** producing structured feedback per file (bug count, severity, line refs). | Each file gets the model's full attention. Structural decomposition fixes dilution — not better prompts or bigger context. | Consistent depth: the last file gets the same detail as the first. | `per_file_pass()` |
| 4 | A **cross-file integration pass** checking data flow, API consistency, pattern consistency. | Per-file passes miss cross-cutting concerns. Batching without integration still misses them. | A separate analysis over per-file summaries finding inconsistent API usage and data-flow problems. | `cross_file_pass()` |
| 5 | **Compare** single vs multi: issues caught, depth consistency across positions. | Proves the argument quantitatively: same model, better architecture, better results. | A table: more total issues in multi-pass, consistent counts across positions, cross-file issues only from integration. | `report()`, `results/comparison.md` |
| 6 | **Record artefacts**: a pattern flagged in one file but identical code approved in another. | The clearest symptom of dilution. The exam's example: `forEach` flagged in file 3, approved in file 11. | At least one such case in single-pass; none in multi-pass. | `artefacts()` |

## What this builds

```
 sample_repo/ (15 .ts files, 24 seeded local bugs, 4 seeded cross-file bugs)
       │
       ├──► SINGLE PASS:  one prompt, all 15 files ─────────────────────► issues[]
       │
       └──► MULTI PASS:   15 prompts, one file each ──► issues[] + summaries[]
                                                              │
                          integration pass over summaries ◄───┘ ─────────► cross_issues[]

 ground_truth.json ──► score both ──► results/comparison.md
```

The scaffolding is what makes this lesson quantitative rather than anecdotal:

- **`make_sample_repo.py`** writes 15 TypeScript files with bugs planted on purpose and records
  every one in `ground_truth.json` with its exact line number. The same `forEach(async …)` line
  appears in **seven** files — including `audit.service.ts`, file 11 in sorted order, the exam's
  example. No hint comments; the model has to find them.
- **Four cross-file bugs** that are invisible from any single file: `getUser(id: string)` is
  called with an object in one file and with two arguments in another; it returns `{ userId }`
  but two callers read `user.user_id` and `user.id`.
- **Scoring** matches a reported issue to ground truth by file + pattern + line (±2). No reliance
  on the model's own count of what it found.

Expected shape of `results/comparison.md` after a live run:

```
| | single pass | multi pass |
| local issues caught / 24 | 14 | 23 |
| cross-file issues caught / 4 | 0 | 3 (integration pass) |
| attention-dilution artefacts | 2 | 0 |

single pass — first 5 files: 8/8 ; last 5 files: 2/7
multi pass  — first 5 files: 8/8 ; last 5 files: 7/7

**single pass**:
- `FOREACH_ASYNC` flagged in ['users.service.ts', 'orders.service.ts', 'inventory.service.ts']
  but missed in ['reports.service.ts', 'cache.service.ts', 'audit.service.ts', 'analytics.service.ts']
```

Exact numbers vary per run. The *shape* — later files thinner, artefacts only in single-pass,
cross-file issues only from the integration pass — is what the lesson is about.

## Files

| File | Purpose |
|---|---|
| `make_sample_repo.py` | Generates `sample_repo/` and `ground_truth.json`. Run once. Committed output included. |
| `code_review.py` | **The lesson.** Single pass, per-file pass, cross-file pass, scoring, artefact detection, report. |
| `test_code_review.py` | Offline tests: ground truth lines are exact; cross-file bugs are invisible per-file; scoring and artefact detection; each pass sends what it should; a simulated "diluted" model shows the expected shape. |
| `sample_repo/`, `ground_truth.json` | The codebase under review and the answer key. |
| `results/` | Written by a live run. |

## How to run

```bash
cd 01-agentic-architecture/06-task-decomposition
python make_sample_repo.py     # already committed; re-run to regenerate
python code_review.py          # 1 + 15 + 1 = 17 API calls
pytest -q
```

## Code walkthrough

### Step 2 — Single pass

```python
blob = "\n\n".join(f"### {name}\n{numbered(src)}" for name, src in files.items())
prompt = f"Review this TypeScript codebase of {len(files)} files ... {blob}"
```

One prompt, fifteen files, line-numbered. The model is asked for every occurrence of every
pattern, in a fixed JSON shape. There's nothing wrong with the prompt. That's the point: the
prompt isn't the problem.

### Step 3 — Per-file pass

```python
for name, src in files.items():
    data = parse_json(ask(f"Review this ONE TypeScript file ... ### {name}\n{numbered(src)}"))
    for i in data.get("issues", []):
        i["file"] = name            # trust the file name we sent, not the model's
```

Same model, same pattern guide, same output format — one file at a time. Each call's full
attention budget goes to one file, so file 15 is reviewed exactly as file 1 was. The per-file
pass also asks for `exports` and `external_calls`: a structured summary of what each file offers
and what it calls into. That's the handoff to the next pass (lesson 1.3's lesson again:
structured, not prose).

### Step 4 — Cross-file integration pass

```python
prompt = ("You are doing the INTEGRATION pass ... You do not see source code — only a "
          "structured summary of each file's exports and the calls it makes into other files. ..."
          + json.dumps(summaries))
```

Per-file passes can't see that `orders.service.ts` calls `getUser({ id })` while
`users.service.ts` declares `getUser(id: string)`. Only a pass that holds *all* the signatures
and *all* the call sites can. It gets summaries, not source — small enough to fit comfortably,
and shaped for the one question it has to answer.

This is the step people skip. "Batching" — splitting 15 files into three prompts of five — is
still a set of local passes. It reduces dilution but finds zero cross-file issues, because no
prompt contains both ends of a mismatch.

### Steps 5–6 — Scoring and artefacts

```python
def artefacts(found):
    """A pattern caught in some files but missed in others where the identical code exists."""
```

For every pattern, split the files where it's seeded into *flagged* and *missed*. If both lists
are non-empty, that's an artefact: identical code, different verdict. `forEach(async …)` is the
canonical one — same line, seven files, and a diluted reviewer flags it early and goes quiet.

`depth_by_position()` reports caught/seeded per file in the order the single pass saw them, and
the report sums the first five and last five. Dilution shows up as the second number being
smaller.

### Fixed pipeline vs adaptive decomposition

This lesson's pipeline is **fixed**: per-file → integration, always, regardless of input. That's
appropriate when the structure of the work is known in advance (every file needs a local
review; the codebase needs one integration check).

**Adaptive** decomposition is when the coordinator decides the breakdown at runtime — lesson 1.2's
coordinator deciding *which* subtopics exist, or a reviewer that reads the file tree first and
groups files by module before deciding how many integration passes to run. Fixed is simpler and
predictable; adaptive handles inputs whose shape you can't predict. The exam wants you to name
which one you're building and why.

## Key takeaways

1. **Attention dilution is architectural, not a capability gap.** Same model, same prompt quality; the difference is how many things one call is asked to look at.
2. **The symptom is a gradient**: detailed → shallow → missing, in input order. Measure it by position.
3. **The tell is an artefact**: identical code, different verdicts. If a reviewer flags a pattern in file 3 and approves it in file 11, it isn't reviewing file 11.
4. **Per-item passes fix depth.** One file per call gives file 15 the attention file 1 got.
5. **Cross-item pass fixes breadth.** Local passes can't see mismatches between files. A separate pass over structured summaries can.
6. **Batching without integration is still local.** Three prompts of five files finds zero cross-file bugs.
7. **Hand off structure, not prose.** The per-file pass emits exports/calls; the integration pass consumes them. Same principle as structured metadata in 1.3.
8. **Score against an answer key.** "The model found 14 issues" means nothing without knowing how many there were.
9. **Fixed pipeline when the shape is known; adaptive when the coordinator must discover it.**

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| "Use a bigger context window" to fix dilution | The files already fit. Capacity isn't the constraint; allocation is. |
| "Write a better prompt" to fix dilution | The prompt is fine. Per-file calls with the *same* prompt are consistent. |
| Batching files into groups and calling it multi-pass | Reduces dilution; still finds zero cross-file issues. |
| Per-file passes with no integration pass | Misses every API/field mismatch. Half the architecture. |
| Integration pass over full source of all files | Reintroduces the dilution you just removed. Use summaries. |
| Trusting the model's reported count | Score against ground truth, or you can't tell 14-of-24 from 14-of-14. |
| Reading one inconsistent verdict as "the model was wrong about file 11" | It's a dilution artefact. The fix is structural, not a re-prompt for file 11. |
| Choosing adaptive decomposition for a task with known structure | Adds coordinator overhead for nothing. Fixed pipeline. |

## Architecture reference

| Strategy | Calls | Depth per file | Cross-file issues | When |
|---|---|---|---|---|
| Single pass | 1 | degrades with position | sometimes, by luck | ≤ ~5 small items |
| Batched | N/k | better, still uneven | no | never, as a final design |
| Per-item only | N | consistent | no | items are independent |
| Per-item + cross-item | N + 1 | consistent | yes | items interact (this lesson) |
| Adaptive | varies | consistent | yes | shape of work unknown up front |
