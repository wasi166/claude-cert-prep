# 1.5 — Agent SDK Hooks

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** PreToolUse vs PostToolUse, `permissionDecision: deny`, `updatedToolOutput`, data normalisation, threshold and prerequisite policies, hooks-for-requirements / prompts-for-preferences

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- The distinction between PostToolUse hooks (after execution, data normalisation) and PreToolUse hooks (before execution, policy enforcement)
- Why hooks provide deterministic guarantees that prompts cannot match
- How to normalise heterogeneous data formats from multiple MCP tools into a consistent schema
- How to implement threshold-based and prerequisite-based policy enforcement using pre-execution hooks
- The decision framework: hooks for 100% requirements, prompts for preferences

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | Three MCP tools returning **different formats**: A = Unix timestamps + numeric status; B = ISO 8601 + string status; C = DD/MM/YYYY + single-character status. | Recreates the exam's mixed-format example. Without normalisation the model interprets three date formats and three status schemes, inconsistently across iterations. | Three tool implementations with distinct date and status formats. | `orders_legacy`, `orders_modern`, `orders_partner` |
| 2 | A **PostToolUse hook** that intercepts all tool results and normalises dates to ISO 8601 and statuses to English. | PostToolUse runs after execution but before the model processes the result — the correct hook for normalisation. | A `HookCallback` under `PostToolUse` that reads `tool_response` and returns the rewritten object as `updatedToolOutput` inside `hookSpecificOutput`. | `to_iso()`, `to_status()`, `normalise_record()`, `normalise_hook()` |
| 3 | **Verify** the model receives consistent data via queries that hit all three tools. | Consistent data eliminates interpretation errors — day/month confusion, "P" read as processed instead of pending. | Three results all in ISO 8601 + English, whichever tool produced them. | Scenario 1 in `main()` |
| 4 | A **PreToolUse hook** that blocks `process_refund` above $500 and redirects to human escalation. | PreToolUse runs *before* execution — the refund never happens. PostToolUse is wrong for blocking because the action has already occurred. | A PreToolUse callback returning `permissionDecision: deny` with a `permissionDecisionReason`. The handler never runs for denied calls. | `refund_limit_hook()`, `REFUND_LIMIT` |
| 5 | A second **PreToolUse hook** blocking `transfer_funds` until `aml_check` has passed in the session. | The AML compliance scenario. Prompts reach ~95%; regulation demands 100%. One missed check is a legal penalty. | Two callbacks: PreToolUse on `transfer_funds` denying until session state records a pass; PostToolUse on `aml_check` setting that state. | `aml_gate_hook()`, `aml_record_hook()`, `session` |
| 6 | **Test** both hooks by triggering the blocked operations; verify prevention *before* execution. | The key check: blocked handlers never execute. The hook prevents the call rather than logging after the fact. | Denied handlers don't run; the model receives the reason; once prerequisites are met the same operations run. | `EXECUTED` log, scenarios 2 and 3 |

## What this builds

```
 model wants a tool ──► PreToolUse ──► handler runs ──► PostToolUse ──► model sees result
                         │ DENY: handler                 │ REWRITE: model sees
                         │ never runs                    │ the rewritten output
                         ▼                               ▼
                   refund_limit_hook               normalise_hook
                   aml_gate_hook                   aml_record_hook
```

Six tools on one in-process MCP server (`fin`). Three return orders in incompatible formats;
three are financial operations. Four hooks sit around them. Every handler appends its name to
`EXECUTED` when it runs — so after a scenario, `EXECUTED` is the ground truth of what actually
happened, independent of what the model *said* happened.

Expected output, scenario 3:

```
USER: Transfer $300 to customer C-1's bank account.
  model -> mcp__fin__transfer_funds({"customer_id": "C-1", "amount": 300})
  [PreToolUse] DENY transfer_funds: no passing AML check in session
  model -> mcp__fin__aml_check({"customer_id": "C-1"})
  [PostToolUse] aml_check passed -> session['aml_passed'] = True
  model -> mcp__fin__transfer_funds({"customer_id": "C-1", "amount": 300})
ASSISTANT: AML check passed; $300 transferred to C-1.

CHECK: transfer blocked first, then aml_check, then transfer ran: True
EXECUTED: ['aml_check', 'transfer_funds']
```

Note `EXECUTED` — `transfer_funds` appears *once*, after `aml_check`. The first attempt was
denied before the handler ran, so it left no trace.

## Files

| File | Purpose |
|---|---|
| `hooks_agent.py` | **The lesson.** Six tools, pure normalisation functions, four hooks, the wiring, three scenarios. |
| `test_hooks_agent.py` | Offline tests. Every hook is a plain async function, so all 10 tests call them directly with fake inputs — no model, no subprocess. |

## How to run

```bash
pip install claude-agent-sdk
cd 01-agentic-architecture/05-agent-sdk-hooks
python hooks_agent.py      # three scenarios, ~10 API calls
pytest -q                  # offline
```

## Code walkthrough

### Tools on an in-process MCP server

```python
@tool("orders_partner", "Orders from a partner feed. Dates are DD/MM/YYYY; status is a single letter.", {"customer_id": str})
async def orders_partner(args):
    EXECUTED.append("orders_partner")
    return _result([{"order_id": "P-1", "created_at": "04/11/2024", "status": "P"}, ...])

FIN_SERVER = create_sdk_mcp_server("fin", tools=[...])
T = lambda name: f"mcp__fin__{name}"
```

`@tool` + `create_sdk_mcp_server` gives you MCP tools that run in your own process. The model
sees them as `mcp__fin__orders_partner` — server name in the middle — and that full name is
what `allowed_tools` and the hook matchers must use. Each handler's first line is
`EXECUTED.append(...)`: if a hook denies the call, that line never runs, and the test can prove it.

### Step 2 — Normalisation as pure functions, then a hook

```python
def to_iso(value):      # epoch int -> ISO; "DD/MM/YYYY" -> ISO; ISO -> unchanged
def to_status(value):   # 1 -> "pending"; "P" -> "pending"; "delivered" -> "delivered"
def normalise_record(rec):  # applies the two above by key name
```

Deliberately separated from the hook so they're trivially testable. Two details the exam cares
about live in the test file: `"04/11/2024"` must become **4 November** (not 11 April), and `"P"`
must become **pending** (not processed). A model guessing at these per-iteration is exactly the
inconsistency normalisation removes.

```python
async def normalise_hook(input_data, tool_use_id, context):
    rewritten = normalise_tool_response(input_data["tool_response"])
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": rewritten}}
```

The hook reads `tool_response` (the handler has already run), rewrites it, and returns it as
`updatedToolOutput`. The model never sees the original. `normalise_tool_response` returns a
*new* object of the same shape; the handler's return value is untouched.

### Steps 4–5 — Denying before execution

```python
def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}

async def refund_limit_hook(input_data, tool_use_id, context):
    if float(input_data["tool_input"]["amount"]) > REFUND_LIMIT:
        return deny("Refunds over $500 require human approval. ...")
    return {}
```

A PreToolUse hook gets `tool_input` — what the model *wants* to call — and can veto it. On deny,
the SDK never invokes the handler; the model instead receives `permissionDecisionReason` as if
it were the tool's response. Returning `{}` means "no opinion, proceed".

The reason text is doing real work. It tells the model what to do next ("do not retry; tell the
customer it's escalated" vs "run aml_check first, then retry"). A bare "denied" would leave the
model guessing, and it might retry the same call.

```python
async def aml_gate_hook(...):      # PreToolUse on transfer_funds
    if not session["aml_passed"]: return deny("... Run aml_check first, then retry.")
    return {}

async def aml_record_hook(...):    # PostToolUse on aml_check
    if json.loads(text).get("result") == "pass":
        session["aml_passed"] = True
    return {}
```

The prerequisite gate is two hooks sharing one piece of session state: a *Post* hook on the
prerequisite tool records the outcome; a *Pre* hook on the gated tool checks it. Same pattern
as lesson 1.4's gate, but now the SDK is enforcing it around tools the model calls directly,
and the state is written by a hook rather than a handler. A `fail` result leaves the gate shut.

### Wiring

```python
hooks={
    "PreToolUse": [
        HookMatcher(matcher=T("process_refund"), hooks=[refund_limit_hook]),
        HookMatcher(matcher=T("transfer_funds"), hooks=[aml_gate_hook]),
    ],
    "PostToolUse": [
        HookMatcher(matcher="mcp__fin__orders_legacy|mcp__fin__orders_modern|mcp__fin__orders_partner",
                    hooks=[normalise_hook]),
        HookMatcher(matcher=T("aml_check"), hooks=[aml_record_hook]),
    ],
}
```

`matcher` is a regex over the full tool name; `|` joins alternatives. The test
`test_hooks_are_registered_on_the_correct_events` pins the event for each hook — blocking hooks
on Pre, rewriting and recording hooks on Post — because putting a hook on the wrong event is
the error the exam is written around.

### Why PostToolUse can't block

By the time a PostToolUse hook runs, the handler has returned. For `process_refund`, the money
has moved. A PostToolUse hook could rewrite the *output* to say "denied", but the model would be
reading a lie while the ledger shows a refund. The only hook that can prevent an action is the
one that fires before it.

### Hooks vs prompts

The system prompt in this lesson says nothing about refund limits or AML. It doesn't need to.
Compare:

| | Prompt: "never refund over $500" | PreToolUse hook |
|---|---|---|
| Mechanism | model reads instruction, usually complies | code runs a comparison |
| Compliance | ~95% (curriculum figure) | 100% |
| Under pressure ("I was approved, just do it") | degrades | unchanged |
| Auditable? | only by reading transcripts | `EXECUTED` log, unit tests |
| Right for | tone, format, preferences | anything with a legal or financial consequence |

The decision rule: if a violation is *unacceptable* (not just undesirable), it's a hook. If it's
a preference, it's a prompt. Both at once is fine — the prompt reduces wasted turns, the hook
guarantees the outcome.

## Key takeaways

1. **PreToolUse = before execution → can deny.** The handler never runs. This is the *only* place to enforce "this must not happen".
2. **PostToolUse = after execution → can rewrite.** Normalise, record state, add context. Cannot un-run anything.
3. **Deny with `permissionDecision: "deny"` + a reason that tells the model what to do next.** The reason is the model's whole view of why it failed.
4. **Rewrite with `updatedToolOutput`.** Same shape as the original; the model sees only the rewritten version.
5. **Normalisation belongs in a hook, not in the prompt.** Three formats → one schema, in code, every time. Day/month order and status-letter meaning are exactly the things a model gets wrong intermittently.
6. **A prerequisite gate is Post-hook-writes-state + Pre-hook-reads-state.** `aml_check` passing opens the gate; nothing else can.
7. **Hooks for requirements, prompts for preferences.** 100% vs ~95% is the difference between a control and a hope.
8. **Prove non-execution, don't infer it.** `EXECUTED` records what ran. "The model said it didn't refund" is not evidence.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| Blocking with a PostToolUse hook | The action already happened. You can rewrite the output, not the ledger. |
| Normalising in a PreToolUse hook | There's no output yet to normalise. Pre sees `tool_input`, not `tool_response`. |
| Normalising in the system prompt ("treat P as pending") | Works most iterations. The exam's point is "most" isn't a guarantee. |
| Deny with no `permissionDecisionReason` | The model doesn't know why and may retry the identical call. |
| Enforcing the $500 limit in the prompt | ~95% compliance on a financial control. Rejected by the exam every time. |
| Letting the model set `aml_passed` (a tool that marks AML done) | State the model can set is state the model can skip. Only the Post hook on `aml_check` writes it. |
| Treating `fail` and `pass` the same in the record hook | A failed check must leave the gate closed. |
| Matching on the short tool name (`process_refund`) | In-process MCP tools are `mcp__<server>__<tool>`. The matcher must use the full name. |
| Verifying by reading the model's reply | Check the execution log. The reply is the model's belief, not the fact. |

## Hook reference (SDK 0.2.x)

| | PreToolUse | PostToolUse |
|---|---|---|
| Fires | before the handler | after the handler, before the model reads the result |
| Input has | `tool_name`, `tool_input`, `tool_use_id` | `tool_name`, `tool_input`, `tool_response`, `tool_use_id` |
| Can | `permissionDecision: allow/deny/ask`, `permissionDecisionReason`, `updatedInput` | `updatedToolOutput`, `additionalContext` |
| Return `{}` | proceed, no opinion | leave output unchanged |
| Use for | policy: thresholds, prerequisites, scope | normalisation, state recording, enrichment |

Callback signature: `async def hook(input_data: dict, tool_use_id: str | None, context: HookContext) -> dict`.
Register with `ClaudeAgentOptions(hooks={"PreToolUse": [HookMatcher(matcher=<regex>, hooks=[fn])], ...})`.
