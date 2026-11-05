# 2.3 — Tool Distribution & Tool Choice

**Domain 2:** Tool Design & MCP Integration (18%) · **Concepts:** scoped toolsets per role, scoped cross-role tools, `tool_choice` auto / any / forced, least-privilege tool design, structural prevention of misuse

## Learning objectives

From the curriculum, after this lesson you should be able to:

- Give each agent role a small, scoped set of tools (the guide's example is 4–5, not 18)
- Implement scoped cross-role tools to avoid coordinator round-trip latency
- Configure `tool_choice` modes (auto, any, forced) for different workflow requirements
- Apply least-privilege tool design by replacing generic tools with constrained alternatives
- Verify that tool distribution prevents cross-role misuse in multi-agent systems

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | **Three roles** (web search, document analysis, synthesis), each with a small scoped toolset (4–5, not 18). | Tool overload degrades selection. 18 tools on one agent is a known misrouting anti-pattern. | A config listing three agents with 4–5 tools each, no tool in more than one role (except scoped cross-role tools). | `ROLES`, `CATALOGUE` |
| 2 | A **scoped `verify_fact`** tool on the synthesis agent for simple lookups. | Routing every verification via the coordinator adds 2–3 round trips (~40% latency). Give the agent a constrained version for the 85% simple case. | `verify_fact` in the synthesis toolset, description limiting it to single-source lookups and saying complex cases go to the coordinator. | `verify_fact()`, its `CATALOGUE` entry |
| 3 | **Forced `tool_choice`** on the document agent so `extract_metadata` runs first. | Forced selection enforces ordering. Three modes: `auto` (free), `any` (some tool), `tool` (this tool). | First call: `{"type": "tool", "name": "extract_metadata"}`; later calls: `auto`. | `ROLES["document-analysis"]["first_call"]`, `run_role()` |
| 4 | Replace generic **`fetch_url`** with constrained **`load_document`** that validates URLs. | Least privilege. A generic fetcher enables misuse; a validating one prevents fetching arbitrary resources. | `load_document` checks extension/trusted domain and rejects others with a clear error. | `load_document()`, `DOCUMENT_EXTENSIONS`, `TRUSTED_DOMAINS` |
| 5 | **Test** with a query needing all three agents; verify no cross-role misuse. | Validates distribution in practice. Synthesis running its own web searches is the common failure. | A log: web-search uses only its tools; doc agent starts with `extract_metadata`; synthesis uses `compile_report` + `verify_fact`; nothing outside its set. | `pipeline()`, `audit()` |

## What this builds

```
 CATALOGUE (15 tools)
 ├── web_search  fetch_snippet  rank_results  extract_entities      ──► web-search agent       tool_choice: any
 ├── load_document  extract_metadata  extract_sections  summarize_section ──► document-analysis  tool_choice: tool=extract_metadata, then auto
 ├── compile_report  cite_sources  check_consistency  verify_fact*  ──► synthesis agent        tool_choice: auto
 └── fetch_url  send_email  run_sql                                 ──► NO ROLE  (exist; never offered)

 * scoped cross-role tool: single-source checks locally, escalate: true for anything bigger
```

Python is the coordinator: it runs the three roles in sequence and passes results forward as
text. Each `run_role()` call sends **only that role's tools** to the API. The model cannot call
a tool it was never given — distribution is enforced by the request, not by instructions.

Expected output:

```
1. web-search agent
    [web-search] web_search({"query": "renewable energy capacity 2024"}) -> {"resultCount": 2, ...
    [web-search] fetch_snippet(...)
2. document-analysis agent (extract_metadata forced first)
    [document-analysis] extract_metadata({"url": "https://docs.example.org/solar-2024.pdf"}) -> ...
    [document-analysis] load_document(...)
    ...
3. synthesis agent (scoped 4 tools)
    [synthesis] verify_fact({"claim": "597 GW", "source_url": "https://docs.example.org/solar-2024.pdf"}) -> {"verified": true
    [synthesis] compile_report(...)

TOOL USAGE BY ROLE
  web-search         first tool_choice={"type": "any"}                      called=['web_search', 'fetch_snippet']
  document-analysis  first tool_choice={"type": "tool", "name": "extract_metadata"}  called=['extract_metadata', 'load_document', ...]
  synthesis          first tool_choice={"type": "auto"}                     called=['verify_fact', 'compile_report']
PASS: no cross-role misuse; mandatory first step honoured.
```

`--overloaded` hands the synthesis agent all 15 tools. Watch for it calling `web_search` or
`load_document` instead of using the findings it was given — the audit will flag it.

## Files

| File | Purpose |
|---|---|
| `tool_distribution.py` | **The lesson.** 15-tool catalogue, three scoped roles, `load_document` vs `fetch_url`, `verify_fact`, `run_role()` with `tool_choice` sequencing, pipeline + audit. |
| `test_tool_distribution.py` | Offline tests: sizes and disjointness; generic tools unassigned; `verify_fact` scope and escalation; `load_document` refusals; `tool_choice` per role and per turn; only role tools sent; audit catches misuse and skipped steps. |

## How to run

```bash
cd 02-tool-design-mcp/03-tool-distribution-choice
python tool_distribution.py                 # ~10 API calls
python tool_distribution.py --overloaded    # synthesis with all 15 tools
pytest -q
```

## Code walkthrough

### Step 1 — Distribution is a data structure

```python
ROLES = {
    "web-search":        {"tools": ["web_search", "fetch_snippet", "rank_results", "extract_entities"], ...},
    "document-analysis": {"tools": ["load_document", "extract_metadata", "extract_sections", "summarize_section"], ...},
    "synthesis":         {"tools": ["compile_report", "cite_sources", "check_consistency", "verify_fact"], ...},
}
```

Four each, out of fifteen. Disjoint — a test asserts it. `fetch_url`, `send_email`, `run_sql`
exist in the catalogue and are assigned to nobody; another test asserts that. Each role's
system prompt also states what it *cannot* do ("You cannot search the web"), so when the model
hits a limit it says so instead of improvising.

Why 4–5? With fifteen tools, every selection is a fifteen-way choice with overlapping
descriptions (`fetch_url` vs `load_document` vs `fetch_snippet`). With four, the choice is
usually obvious. Selection reliability is a function of how many plausible options there are.

### Step 2 — The scoped cross-role tool

```python
"verify_fact": ("Verify ONE simple factual claim against ONE already-known source URL (single-source lookup). "
                "Use for quick checks like 'does source X say 597 GW?'. Do NOT use for claims needing several "
                "sources, new web research, or judgement calls — return those to the coordinator instead "
                "(the result will say escalate: true).", ...)
```

Verification is conceptually a research capability — the web-search agent's domain. Strictly
partitioned, every "does this number check out?" would go synthesis → coordinator → web-search →
coordinator → synthesis. Instead the synthesis agent gets a *constrained* verifier: one claim,
one known URL, local store only. The handler returns `escalate: true` when the source isn't
local, so the 15% complex case still routes correctly. The description says all of this
(lesson 2.1: the description is the router).

### Step 3 — `tool_choice` as workflow control

```python
"web-search":        "first_call": {"type": "any"}
"document-analysis": "first_call": {"type": "tool", "name": "extract_metadata"}
"synthesis":         "first_call": {"type": "auto"}

choice = cfg["first_call"] if turn == 0 else {"type": "auto"}
```

| Mode | Guarantees | Used here for |
|---|---|---|
| `auto` | nothing — model may answer in text | synthesis: it should reason, and tools are optional |
| `any` | *some* tool is called | web-search: must search, not recall from training |
| `tool` + `name` | *this* tool is called | doc-analysis: metadata extraction is a mandatory first step |

The forced choice applies to the **first call only**. After that, `auto` — otherwise the agent
would call `extract_metadata` forever. `run_role()` records the `tool_choice` it sent each turn
so the test can assert `[forced, auto, ...]`.

Forcing is enforcement, not suggestion. "Always extract metadata first" in the system prompt is
the ~95% version (lesson 1.4); `tool_choice: tool` is the 100% version.

### Step 4 — Least privilege: `load_document` replaces `fetch_url`

```python
def load_document(url):
    p = urlparse(url)
    if p.scheme != "https":                       return _err("validation", ...)
    if p.netloc not in TRUSTED_DOMAINS:           return _err("permission", ...)
    if not p.path.endswith(DOCUMENT_EXTENSIONS):  return _err("validation", ...)
    return json.dumps({"url": url, "body": ..., "loaded": True})
```

`fetch_url` fetches anything: an internal admin endpoint, an attacker's URL embedded in a
document, a 2 GB binary. `load_document` accepts https + trusted host + document extension, and
refuses everything else with a structured error (lesson 2.2's shape, `isRetryable: false`). The
description tells the model the constraint up front, so it doesn't try. The handler enforces it
regardless.

The principle: a tool's capability should be exactly as wide as the role needs. "Fetch" is
wider than "load a document from our document store".

### Step 5 — Structural prevention, then audit

```python
tools = tools_for(names)              # only this role's tools go in the request
...
if b.name not in names:               # cannot happen via the API; audit anyway
    raise CrossRoleMisuse(...)
```

The API only lets the model call tools present in the request. A synthesis agent given four
tools *cannot* call `web_search`; there's no such tool from its point of view. The in-loop check
and the end-of-run `audit()` exist to prove it in the log and to catch configuration mistakes
(someone adding `web_search` to the synthesis list).

Compare `--overloaded`: same synthesis prompt, all 15 tools offered. Now `web_search` *is*
available, and an agent that was handed findings may decide to redo the research. That's the
misuse; the fix was never giving it the option.

## Key takeaways

1. **Small, scoped, disjoint.** 4–5 tools per role from a larger catalogue. Fewer plausible options → more reliable selection.
2. **Distribution is enforced by the request.** Tools not in `tools=[...]` don't exist for that call. Instructions are a backup; omission is the guarantee.
3. **Scoped cross-role tools cut round trips.** A constrained version for the common simple case, with explicit escalation for the rest. The description states the scope.
4. **`tool_choice` has three modes:** `auto` (optional), `any` (some tool), `tool`+`name` (this tool). Pick by what the workflow *requires*.
5. **Force on the first call, then `auto`.** A forced tool_choice left on loops forever.
6. **Replace generic with constrained.** `fetch_url` → `load_document` with scheme, host, and extension checks. Capability should match the role's actual need.
7. **Dangerous tools stay in the catalogue unassigned.** Existing ≠ offered.
8. **Audit from the log, not the transcript.** Record what each role called; check it against the role's set and the required ordering.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| One agent with all 18 tools | Every selection is an 18-way choice with overlaps; misrouting is routine. |
| Strict partitioning with no cross-role tools | Every simple cross-domain check becomes 2–3 coordinator round trips. |
| An unscoped cross-role tool (giving synthesis full `web_search`) | Synthesis redoes research instead of synthesising. Scope it or don't share it. |
| Relying on "always extract metadata first" in the prompt | ~95%. `tool_choice: {"type": "tool", ...}` is 100%. |
| Leaving forced `tool_choice` on for every turn | The agent calls the same tool forever. First call only. |
| Using `any` when a specific tool is required | `any` guarantees *a* tool, not *the* tool. |
| Using `auto` when a tool call is mandatory | The model may answer from memory. |
| Generic `fetch_url` because "the prompt says only fetch documents" | The handler fetches anything the model asks for. Constrain the tool, not the prompt. |
| Verifying scoping by reading the agent's reply | Check the recorded tool calls against the role's set. |

## `tool_choice` reference

| Value | Meaning | Typical use |
|---|---|---|
| `{"type": "auto"}` | model may call zero or more tools | default; reasoning agents |
| `{"type": "any"}` | model must call at least one tool | "don't answer from memory" |
| `{"type": "tool", "name": "X"}` | model must call X | mandatory first step; structured output extraction |
| `{"type": "none"}` | model may not call tools | force a text answer |
