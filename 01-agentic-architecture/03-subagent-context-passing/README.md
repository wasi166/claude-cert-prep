# 1.3 — Subagent Invocation and Context Passing

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** Agent SDK subagents, `allowed_tools` + `Agent`, `AgentDefinition`, structured metadata, attribution failures, parallel spawning, `fork_session`

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- Why the coordinator's `allowedTools` must include `Task` (or `Agent`, its name since Claude Code v2.1.63) to spawn subagents
- How to design structured metadata that separates content from source attribution
- Why context passing failures cause attribution errors in downstream agents
- How to spawn independent subagents in parallel for reduced latency
- The difference between `fork_session` and parallel Task tool invocation

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | Create a coordinator with **`Task` (or `Agent`) in its `allowedTools`**. | A binary requirement. Without it the coordinator cannot invoke any subagent. Not optional, not configurable at runtime. | A `query()` call whose options include `allowedTools` containing `Agent`, plus subagent definitions under `options.agents`. | `build_options()` → `allowed_tools=["Agent"]`, `agents=AGENTS` |
| 2 | Define **two subagents**: web search (returns source URLs + titles) and document analysis (returns page references). | Each needs scoped tool access matching its role. The exam tests proper `AgentDefinition` fields: description, system prompt, tool restrictions. | Two `AgentDefinition`s, each with description, prompt, and a restricted tool set. Web agent: search tools only. Doc agent: file-reading tools only. | `AGENTS["web-searcher"]`, `AGENTS["doc-analyst"]` |
| 3 | Design a **structured output** separating content from metadata: `claim`, `source_url`, `document_name`, `page_number`, `confidence`. | When a synthesis agent produces unsourced claims, the root cause is that the coordinator passed content without structured metadata. Separation is the fix. | A JSON schema for `Finding` with content fields (`claim`, `analysis`) and metadata fields (`source_url`, `document_name`, `page_number`, `confidence`, `retrieved_by`). | `FINDING_SCHEMA` |
| 4 | Pass **complete structured results** from both subagents to a synthesis subagent, **preserving all metadata**. | The critical step. Stripping metadata before synthesis is the root cause of attribution failures. | The coordinator passes the full findings array to the synthesis agent. No fields stripped or summarised away. | `coordinator_system_prompt()` step 3; `AGENTS["synthesizer"]` |
| 5 | **Verify** the synthesis agent attributes every claim to a specific source with URL and page number. | Confirms context passing worked. If a claim lacks attribution, trace back to whether metadata was actually passed — don't blame the synthesis prompt. | A report where every claim has a citation with URL or document+page. | `verify_attribution()`, the `__main__` block |
| 6 | **Refactor** the coordinator to spawn both research subagents **in parallel** via multiple Task calls in a single response. | Latency awareness. Sequential spawning of independent subagents wastes time. | Both research subagents invoked simultaneously, coordinator waits for both before synthesis. | `coordinator_system_prompt(parallel=True)`, `is_parallel()` |

## What this builds

```
                 ┌──────────────────────────────────────────┐
  topic ───────► │  COORDINATOR  (a Claude session)         │
                 │  allowed_tools = ["Agent"]               │
                 │  agents = {web-searcher, doc-analyst,    │
                 │            synthesizer}                  │
                 └───┬──────────────┬───────────────┬───────┘
          one turn,  │              │               │  later turn
          two calls  ▼              ▼               ▼
              ┌────────────┐ ┌────────────┐  ┌─────────────┐
              │web-searcher│ │doc-analyst │  │ synthesizer │
              │WebSearch   │ │Read, Glob  │  │ (no tools)  │
              │WebFetch    │ │Grep        │  │             │
              └─────┬──────┘ └─────┬──────┘  └──────▲──────┘
                    │ findings[]   │ findings[]     │
                    └──────────────┴────────────────┘
                        complete JSON, metadata intact
```

The big shift from lesson 1.2: there, *our Python* was the coordinator and called the API for
each spoke. Here the coordinator is **itself a Claude session** that spawns subagents by calling
the built-in `Agent` tool. Our code configures it (system prompt, subagent definitions, allowed
tools), watches the message stream, and verifies the output.

Expected output in parallel mode:

```
[  2.1s] coordinator spawned ['web-searcher', 'doc-analyst']  <- parallel
[ 38.4s] coordinator spawned ['synthesizer']
[ 51.0s] done. turns=4 cost=$0.0612

REPORT
- Global solar PV additions reached 597 GW in 2024 ... [source: iea_solar_outlook_2025.md, p.1]
- Offshore wind added 11 GW in 2024 ... [source: gwec_wind_report_2025.md, p.2]
- ... [source: https://www.iea.org/reports/renewables-2024]

VERIFICATION
spawn batches : [['web-searcher', 'doc-analyst'], ['synthesizer']]  ->  PARALLEL
cited claims  : 7
uncited claims: 0
PASS: every claim is attributed to a URL or a document+page.
```

With `--sequential` you'll see three spawn batches instead of two and a noticeably longer
wall-clock time — the only difference is one sentence in the coordinator's system prompt.

## Files

| File | Purpose |
|---|---|
| `context_passing.py` | **The lesson.** Finding schema, three `AgentDefinition`s, coordinator options, stream observer, attribution verifier. |
| `test_context_passing.py` | Offline tests: `Agent` in allowed tools; tools scoped per role; schema separates content/metadata; coordinator told to pass findings intact; verifier catches uncited claims; parallel detection. |
| `docs/` | Two short "reports" with `--- page N ---` markers for the doc-analyst to read. |

## How to run

```bash
pip install claude-agent-sdk          # bundles the Claude Code CLI; nothing else to install
cd 01-agentic-architecture/03-subagent-context-passing
python context_passing.py             # parallel
python context_passing.py --sequential
pytest -q                             # offline
```

A live run spawns three subagents and the web-searcher does real searches; expect ~1 minute
and a few cents.

## Code walkthrough

### Step 3 first — the Finding schema, because everything else is built around it

```python
FINDING_SCHEMA = {
    "properties": {
        # --- content ---
        "claim":         ...,
        "analysis":      ...,
        # --- metadata (source attribution) ---
        "source_url":    ...,
        "document_name": ...,
        "page_number":   ...,
        "confidence":    ...,
        "retrieved_by":  ...,
    },
    "required": [all seven],
}
```

Content and metadata are *separate fields*, and all are required (`null` where not applicable).
Why this matters: if a subagent returns "Solar hit 597 GW in 2024 (IEA report, page 1)" as prose,
the coordinator has to parse the source back out — and under pressure to be concise it will
simply drop it. With the source in its own field, "pass the findings" and "pass the sources"
are the same action.

`FINDINGS_INSTRUCTION` embeds this schema in both research subagents' prompts, so they return the
same shape and the synthesizer handles one format.

### Step 2 — Three `AgentDefinition`s

```python
"web-searcher": AgentDefinition(
    description="Searches the web for current facts and returns findings with source URLs.",
    prompt="...record the exact page URL in source_url...",
    tools=["WebSearch", "WebFetch"],
    model="sonnet",
),
"doc-analyst": AgentDefinition(
    description="Reads local documents in ./docs and returns findings with document name and page number.",
    prompt="...Documents contain '--- page N ---' markers...",
    tools=["Read", "Glob", "Grep"],
    model="sonnet",
),
"synthesizer": AgentDefinition(
    description="Writes a cited report from a JSON array of findings.",
    prompt="...EVERY bullet must ... end with a citation built from that finding's metadata...",
    tools=[],
    model="sonnet",
),
```

Three fields do the work:

- **`description`** is what the *coordinator* reads when deciding which subagent to call. It's
  the tool description, from the coordinator's point of view.
- **`prompt`** is the subagent's system prompt — its whole identity, since it starts with an
  empty context.
- **`tools`** scopes access. The web agent can't read files; the doc agent can't search; the
  synthesizer can do nothing but think. This is least privilege, and it also makes failures
  diagnosable: a doc-analyst that cites a URL has *invented* it, because it had no way to fetch one.

### Step 1 — `allowed_tools=["Agent"]`

```python
ClaudeAgentOptions(
    system_prompt=coordinator_system_prompt(parallel),
    agents=AGENTS,
    allowed_tools=["Agent"],       # <- without this, no subagent can be spawned
    permission_mode="bypassPermissions",
    cwd=str(HERE),
)
```

`Agent` is the built-in tool the coordinator calls to spawn a subagent (`subagent_type`,
`prompt`, `description`). Before Claude Code v2.1.63 it was called `Task`; `Task` still works
as an alias, but new code should say `Agent`. If it's not in `allowed_tools`, every delegation
attempt is blocked and the coordinator will try to do the research itself with no tools —
badly. The exam treats this as a yes/no fact.

Note `agents=AGENTS` registers the definitions and `allowed_tools` permits the spawning tool.
You need both.

### Step 4 — The coordinator's instructions

```python
3. Invoke synthesizer ONCE. Its prompt must contain the COMPLETE findings from both subagents,
   as raw JSON, with every field intact (claim, analysis, source_url, document_name,
   page_number, confidence, retrieved_by). Do NOT summarise, paraphrase, or strip any field.
```

This is the sentence the whole lesson is about. The coordinator, being an LLM, will by default
"helpfully" summarise the two findings arrays into a tidy paragraph before passing them on —
and that paragraph will have no page numbers. The instruction names every field and forbids
summarising. The synthesizer then has what it needs, and its own prompt (*never write a bullet
without a citation, never invent a source*) can be strict because the inputs make it possible.

### Step 5 — Verification, and where to look when it fails

```python
CITATION = re.compile(r"\[source: (https?://\S+?|[^\]]+?, p\.\d+)\]")
cited, uncited = verify_attribution(report)
```

Every bullet must end in `[source: <url>]` or `[source: <file>, p.<n>]`. The failure message
is deliberate:

> FAIL: uncited claims. Trace back: did the coordinator pass the findings JSON to the
> synthesizer with metadata intact? (Check that before blaming the synthesizer.)

The exam's diagnostic: an unsourced claim in the synthesis output is a *context passing*
failure upstream, not a synthesis prompt failure. The synthesizer can't cite what it wasn't given.

### Step 6 — Parallel spawning

```python
spawn_rule = (
    "Invoke web-searcher AND doc-analyst in the SAME response (two Agent tool calls at once) "
    "because they are independent. Wait for both results before continuing."
    if parallel else
    "Invoke web-searcher first, wait for its result, then invoke doc-analyst."
)
```

Parallelism in the SDK isn't a flag — it's the coordinator emitting two `Agent` tool_use blocks
in one assistant message. We get that by telling it to, and we *detect* it in the stream:

```python
if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
    spawned = [b.input.get("subagent_type") for b in message.content
               if isinstance(b, ToolUseBlock) and b.name in ("Agent", "Task")]
```

`parent_tool_use_id is None` filters to the coordinator's own turns (messages from inside a
subagent carry the id of the Agent call that spawned them). Two names in one `spawned` list
means parallel. `is_parallel()` checks that both research agents landed in the same batch.

### `fork_session` is not this

`ClaudeAgentOptions(fork_session=True)` only applies with `resume=<session_id>`: instead of
continuing that session in place, it branches a *new* session id off it. It's a session-history
operation — "keep the old transcript, start a new one from here" — and has nothing to do with
spawning subagents. Parallel subagents are separate agent instances with fresh contexts, created
by `Agent` tool calls. The exam lists both terms because they sound related and aren't.

## Key takeaways

1. **`Agent` (née `Task`) in `allowed_tools` is a hard requirement** for spawning subagents. Register definitions with `agents=`, permit spawning with `allowed_tools=`. Both.
2. **An `AgentDefinition` is description + prompt + tools.** Description is for the coordinator's choice; prompt is the subagent's entire context; tools are least-privilege.
3. **Separate content from metadata in the schema.** `claim` and `source_url` as sibling fields, all required. Attribution then survives handoffs because it isn't buried in prose.
4. **Pass findings intact.** Tell the coordinator, by field name, not to summarise or strip. The default LLM behaviour is to condense, and condensing drops citations.
5. **Unsourced claims are a context-passing failure.** Diagnose upstream (what did the synthesizer receive?) before touching the synthesis prompt.
6. **Parallel = multiple `Agent` calls in one coordinator turn.** Independent subagents should be spawned together; the latency saving is the whole point.
7. **`fork_session` ≠ parallel subagents.** It branches a resumed session's history. Unrelated to delegation.
8. **Tool scoping makes fabrication detectable.** A doc-analyst with no web tools that cites a URL has invented it.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| Leaving `Agent`/`Task` out of `allowed_tools` | The coordinator cannot spawn anything. No runtime workaround. |
| Defining subagents in `agents=` but forgetting `allowed_tools=["Agent"]` | Same failure — registration isn't permission. |
| Giving every subagent all tools | Loses least privilege and loses the ability to tell fabricated sources from real ones. |
| Subagents return findings as prose | Metadata gets lost at the first summarisation step. |
| Coordinator summarises findings before synthesis | Strips `source_url` / `page_number`. The classic attribution failure. |
| Fixing unsourced output by hardening the synthesis prompt | Wrong layer. The synthesizer can't cite what it wasn't given. |
| Spawning independent subagents one after another | Doubles latency for no benefit. Independent work goes in one turn. |
| Treating `fork_session` as a way to run subagents in parallel | It's a resume option that branches session history. |
| Passing only `claim` strings to the synthesizer "to save tokens" | Saves tokens, loses every citation. |

## SDK reference (as of SDK 0.2.x)

| Thing | Where | Notes |
|---|---|---|
| Spawn tool name | `allowed_tools=["Agent"]` | `Task` = alias from before v2.1.63 |
| Subagent definition | `AgentDefinition(description, prompt, tools, model, ...)` | multi-word fields are camelCase (`maxTurns`, `permissionMode`) |
| Register subagents | `ClaudeAgentOptions(agents={name: AgentDefinition})` | name is what the coordinator passes as `subagent_type` |
| Detect a spawn | `ToolUseBlock.name in ("Agent", "Task")`, `.input["subagent_type"]` | on an `AssistantMessage` with `parent_tool_use_id is None` |
| Inside a subagent | `message.parent_tool_use_id` is set | id of the Agent call that spawned it |
| Final answer | `ResultMessage.result` | `None` on error subtypes; check `is_error` |
| `fork_session` | `ClaudeAgentOptions(resume=..., fork_session=True)` | branches a new session id off a resumed one |
