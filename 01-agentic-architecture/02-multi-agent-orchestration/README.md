# 1.2 — Multi-Agent Orchestration

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** hub-and-spoke, coordinator responsibilities, subagent isolation, explicit context passing, broad decomposition, iterative refinement

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- How hub-and-spoke architecture centralises all communication through a coordinator
- Why subagent isolation means every piece of context must be explicitly passed
- How to implement broad task decomposition that avoids a decomposition so narrow that whole subtopics are missed
- How iterative refinement loops detect and fill coverage gaps
- Why tracing failures to the coordinator decomposition is the correct diagnostic approach

## Problem statement

Build a hub-and-spoke research coordinator in six steps.

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | Create a **coordinator agent** that accepts a broad research topic. | The coordinator is the hub. It owns decomposition, subagent selection, and aggregation — not the subagents. | A coordinator function that takes a topic string and returns a structured report, with a system prompt defining its role as the orchestrating hub. | `coordinator()`, `COORDINATOR_SYSTEM` |
| 2 | **Decompose** the topic into at least 5 distinct subtopics covering the full breadth of the subject. | Narrow decomposition is a specific exam failure pattern. A coordinator that only assigns solar and wind misses entire categories; incomplete output traces back here. | A decomposition function producing 5+ subtopics. For renewable energy: solar, wind, geothermal, tidal, biomass, fusion at minimum. | `decompose()`, `MIN_SUBTOPICS` |
| 3 | Spawn **two subagents** (web search, document analysis) with **explicit context passing** — all relevant information in each prompt. | Subagent isolation: no shared memory, no inherited context. If a subagent does poorly, check what the coordinator gave it, not the subagent. | Two subagent invocations, each receiving the full subtopic, the research goal, and relevant context from prior agents — all in the prompt. | `web_search_subagent()`, `document_analysis_subagent()`, `delegate()` |
| 4 | **Aggregate** both subagents' results and **evaluate coverage completeness**. | The coordinator must judge whether combined results span the original topic. Gaps found here trigger re-delegation. | An aggregation step producing a coverage assessment: which subtopics are covered, partial, or missing. | `evaluate_coverage()` |
| 5 | **Iterative refinement loop**: when gaps are found, re-delegate with targeted queries and re-evaluate until coverage is sufficient. | One round of delegation is not enough. Evaluating output and re-delegating for gaps is what distinguishes a coordinator from a dispatcher. | A loop that checks coverage, identifies gaps, sends follow-up queries for missing subtopics, and re-evaluates until a threshold is met or a max iteration count is reached. | the `for round_no` loop in `coordinator()`, `MAX_ROUNDS` |
| 6 | **Test** with "renewable energy technologies" and verify the output covers solar, wind, geothermal, tidal, biomass, and fusion. | Matches the exam pattern where too-narrow decomposition misses subtopics. If only solar and wind appear, the root cause is the decomposition. | A report with substantive sections on all six, and a coverage evaluation showing 100%. | `REQUIRED`, the `__main__` block |

## What this builds

```
                    ┌──────────────────┐
     topic ───────► │   COORDINATOR    │ ───────► research report
                    │  decompose       │
                    │  delegate        │
                    │  evaluate        │
                    │  refine          │
                    └───┬──────────┬───┘
                        │          │
                 ┌──────▼───┐  ┌───▼───────┐
                 │ web      │  │ document  │
                 │ search   │  │ analysis  │
                 └──────────┘  └───────────┘
```

The coordinator is the only component that talks to anyone. The two spokes are plain, stateless
API calls with their own system prompts. They never see the coordinator's history or each other's
output unless the coordinator writes it into their prompt.

Expected output with a good decomposition:

```
[coordinator] decomposed into 7 subtopics: ['solar', 'wind', 'hydro', 'geothermal', 'biomass', 'tidal', 'fusion']
[coordinator] delegating 'solar' to both subagents
...
[coordinator] round 1: coverage 100%  gaps=[]
...
PASS: all six required technologies present.
```

With `--narrow`, the coordinator is told to decompose badly, and you watch the rest of the system
compensate:

```
[coordinator] decomposed into 2 subtopics: ['solar', 'wind']
[coordinator] round 1: coverage 28%  gaps=['geothermal', 'tidal', 'biomass', 'fusion', 'hydro']
[coordinator]   re-delegating 'geothermal' with a targeted query
...
[coordinator] round 2: coverage 100%  gaps=[]
PASS: all six required technologies present.
```

Refinement rescued the run — but it cost a whole extra round of subagent calls. The *fix* is the
decomposition prompt, not more refinement.

## Files

| File | Purpose |
|---|---|
| `research_coordinator.py` | **The lesson.** Hub, two spokes, stubbed search index and document corpus. |
| `test_research_coordinator.py` | Offline tests: context is passed explicitly; narrow decomposition is caught and refined; refinement is bounded. |

## How to run

```bash
cd 01-agentic-architecture/02-multi-agent-orchestration
python research_coordinator.py             # good decomposition, expect 100% in round 1
python research_coordinator.py --narrow    # bad decomposition, watch refinement fill the gaps
pytest -q                                  # offline, no key needed
```

A full run makes roughly 20 API calls (1 decompose + 2 spokes × N subtopics + evaluations + report).

## Code walkthrough

### The one helper: `ask()`

```python
def ask(system, user, max_tokens=800):
    response = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": user}])
```

Every call in this file goes through `ask()`, and every call sends a **one-message history**.
That is the whole mechanism of isolation: there is no shared `messages` list anywhere. If a
spoke needs to know something, it has to be in `user`.

### Step 3 — The spokes

```python
def web_search_subagent(topic, goal, subtopic, known_so_far):
    snippets = search_stub(subtopic)
    return ask(
        system="You are a web research subagent. You have NO memory of any other task. ...",
        user=f"""Overall research topic: {topic}
Research goal: {goal}
Your assigned subtopic: {subtopic}
Already covered by other subagents (do not repeat): {...}

Search results for "{subtopic}":
{snippets}
...""")
```

Look at the signature. The spoke takes `topic`, `goal`, `subtopic`, and `known_so_far` as
*parameters* — it cannot reach into the coordinator for them. The prompt then restates all four.
That's explicit context passing: redundant-looking, and exactly right.

The document spoke is told to say "No documents provided" when the corpus is empty for its
subtopic (the stub deliberately has no documents for tidal or fusion). A spoke that invents
material when starved of context is worse than one that reports the gap — the coordinator can
act on a reported gap.

### Step 1 — The hub's system prompt

```python
COORDINATOR_SYSTEM = (
    "You are the coordinator of a hub-and-spoke research team. You own task decomposition, "
    "delegation to subagents, evaluation of coverage, and the final report. Subagents have no "
    "memory and cannot see each other; you must pass them everything they need. You are "
    "responsible for breadth: a report that misses whole categories of the field is your failure."
)
```

Four responsibilities named, and the last sentence assigns blame in advance. When the output
is incomplete, this prompt says whose fault it is.

### Step 2 — Decomposition, and how to get it wrong

```python
if narrow:
    instruction = f"List the 2 most well-known examples of '{topic}'."
else:
    instruction = (f"Decompose the topic '{topic}' into at least {MIN_SUBTOPICS} distinct subtopics "
                   "that together cover the FULL breadth of the field — include established, "
                   "niche, and emerging/experimental categories, not just the most popular ones.")
```

The two prompts differ by one idea: *breadth*. The narrow one asks for the famous examples and
gets solar and wind. The good one asks for established + niche + experimental and gets fusion
and tidal too. Everything downstream is identical. **If the final report is missing categories,
look here first** — that's the diagnostic the exam wants.

### Step 4 — Coverage evaluation

```python
def evaluate_coverage(topic, findings) -> dict[str, str]:
    ...
    "Think about every major category of '{topic}' that a complete report must include —
     not just the ones above. For each, rate coverage as covered / partial / missing."
```

The coordinator doesn't grade itself against its own subtopic list — that would just confirm
whatever it already decided. It asks "what *should* a complete report on this topic include?"
and grades the findings against that. This is what lets it detect that the narrow decomposition
missed geothermal: geothermal was never on the list, but the evaluator knows it should be.

### Step 5 — Refinement loop

```python
for round_no in range(1, MAX_ROUNDS + 1):
    coverage = evaluate_coverage(topic, findings)
    gaps = [s for s, status in coverage.items() if status != "covered"]
    if not gaps:
        break                                   # normal exit
    for gap in gaps:
        findings[gap] = delegate(topic, goal, gap, known_so_far=list(findings))
else:
    print("WARNING: hit MAX_ROUNDS ...")
```

Evaluate → find gaps → re-delegate *only the gaps*, with the now-larger `known_so_far` so the
spokes don't duplicate → evaluate again. The `break` on zero gaps is the normal exit;
`MAX_ROUNDS` is the safety bound (same pattern as lesson 1.1's iteration cap). The `for/else`
means the warning fires only when the loop exhausts without breaking.

A dispatcher would stop after the first `delegate` pass. The evaluate-and-re-delegate loop is
what makes this a coordinator.

### Step 6 — The test

```python
REQUIRED = ["solar", "wind", "geothermal", "tidal", "biomass", "fusion"]
missing = [r for r in REQUIRED if r not in report.lower()]
```

The exam's canonical case. The failure message says where to look: *"check the coordinator's
decomposition first."*

## Key takeaways

1. **The coordinator owns four things:** decomposition, delegation, evaluation, refinement. Subagents own none of them.
2. **Isolation is structural, not a setting.** Each spoke is a fresh call with a one-message history. There is nothing to "share" — so everything must be passed.
3. **Explicit context passing means restating the obvious.** Topic, goal, assigned subtopic, what others have done — in every prompt, every time.
4. **Breadth is the decomposition's job.** "At least N subtopics covering established, niche, and emerging categories" vs "the main examples" is the difference between a complete report and a two-section one.
5. **Evaluate against what should exist, not what you planned.** A coverage check that only grades the planned subtopics can't find the ones you forgot.
6. **Refinement is a safety net, not a strategy.** It catches gaps, but each round costs a full set of spoke calls. If refinement regularly fires, fix the decomposition.
7. **Diagnose from the hub outward.** Thin subagent output → what did the coordinator pass it? Missing sections → what did the coordinator decompose? The spoke is almost never the root cause.
8. **Bound the refinement loop** and report honestly when the bound is hit — same lesson as 1.1's iteration cap.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| Decomposing into "the main types" / "the most well-known examples" | Produces solar and wind; misses geothermal, tidal, biomass, fusion. The classic narrow-decomposition failure. |
| Blaming the subagent when its output is thin | Subagents only know what they were told. Check the prompt the coordinator built first. |
| Sharing one `messages` list between coordinator and subagents | Breaks isolation; context leaks implicitly and the design becomes untestable. |
| Letting subagents call each other | No longer hub-and-spoke. All communication routes through the coordinator. |
| Delegating once and writing the report | That's a dispatcher. A coordinator evaluates and re-delegates. |
| Grading coverage only against the planned subtopic list | Can never detect subtopics that were never planned. |
| Re-delegating *everything* on a gap | Wasteful. Re-delegate only the gaps, and tell the spokes what's already covered. |
| Unbounded refinement loop | Same failure as an unbounded agentic loop. Bound it, warn when the bound is hit. |
| Subagent invents content when given no material | Hides the gap from the coordinator. A spoke should report "no documents provided", not fabricate. |

## Roles reference

| Responsibility | Owner | In the code |
|---|---|---|
| Accept the task | Coordinator | `coordinator(topic)` |
| Decompose into subtopics | Coordinator | `decompose()` |
| Choose which spoke handles what | Coordinator | `delegate()` |
| Build each spoke's prompt with full context | Coordinator | `web_search_subagent()` / `document_analysis_subagent()` parameters |
| Do one bounded piece of work | Spoke | the `ask()` call inside each subagent |
| Aggregate and judge coverage | Coordinator | `evaluate_coverage()` |
| Re-delegate for gaps | Coordinator | refinement loop in `coordinator()` |
| Write the final report | Coordinator | `write_report()` |
