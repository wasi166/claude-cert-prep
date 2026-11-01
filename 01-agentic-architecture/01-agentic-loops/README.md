# 1.1 — Agentic Loops

**Domain 1:** Agentic Architecture & Orchestration (27%) · **Concepts:** `stop_reason`, `tool_use` / `tool_result`, multi-tool selection, safety iteration caps

## Learning objectives

From the curriculum, after this lesson you should be able to explain:

- How the agentic loop lifecycle works with the Messages API
- Why `stop_reason` is the authoritative signal for loop control
- How to handle the `tool_use` and `end_turn` stop reasons correctly
- How to append tool results to conversation history for multi-turn execution
- When safety iteration caps are appropriate versus inappropriate as stopping mechanisms

## Problem statement

The exercise asks for an agent built in six steps. Each one maps to a specific part of the code.

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | Set up a Claude API client with two tools: a **calculator** (accepts `expression`, returns result) and a **web search stub** (accepts `query`, returns mock results). | Multi-tool setups expose model-driven decision-making — Claude must pick the right tool from context, which is core to agentic architecture. | Two tool definitions with proper JSON Schema `input_schema`, each with `name`, `description`, and parameters. | `Tool`, `ToolRegistry`, `TOOLS` |
| 2 | Implement the agentic loop that sends requests to Claude and **inspects `stop_reason`** after each response. | The exam tests whether you use `stop_reason` (deterministic) versus content-type checks or natural-language parsing (unreliable). | A `while` loop that calls `client.messages.create()` and checks `response.stop_reason` each iteration. | `Agent.run()` |
| 3 | Handle `tool_use`: execute the requested tool, create a tool result message, append it to history. | The critical step — the exam checks that you extract tool calls, execute them, and return results in the right message format. | On a tool request, the code extracts the `tool_use` block, runs the matching function, and appends both the assistant response and a user message containing `tool_result`. | `tool_use` branch in `Agent.run()` |
| 4 | Handle `end_turn`: extract and return the final response. | `end_turn` is Claude's signal that the task is complete; extracting the text correctly ends the loop. | When `stop_reason` is `end_turn`, the loop exits and returns the text content. | `end_turn` branch in `Agent.run()` |
| 5 | Test with a prompt that needs **multiple sequential tool calls** (search for a value, then calculate with it) and verify the loop continues through all iterations. | Sequential calls test the full lifecycle: complete one tool call, receive the result, reason about it, decide to call another, then return. | At least two tool-call iterations before `end_turn`: search first, use the result in a calculation, return the combined answer. | `DEFAULT_PROMPT` in `main()` |
| 6 | Add a **safety iteration cap of 20** as a maximum bound (not the primary stopping mechanism) and log a warning if it triggers. | The exam distinguishes safety caps (acceptable fallback) from caps as the primary stop (anti-pattern). The cap should never trigger in normal operation. | A `MAX_ITERATIONS` constant, a counter incremented each loop, and a warning log if the cap is hit. | `MAX_ITERATIONS`, top of `Agent.run()` |

## What this builds

A minimal but production-shaped agent: Claude is given two tools (a calculator and a stubbed web
search), and a `while` loop keeps calling the Messages API, executing whatever tools Claude asks
for, until Claude signals it is finished. The default test prompt forces two *sequential* tool
calls — search for a value, then do arithmetic on it — so you can watch the full lifecycle:

```
iter=1 stop_reason=tool_use   -> web_search({"query": "Eiffel Tower height"})
iter=2 stop_reason=tool_use   -> calculator({"expression": "330 * 3"})
iter=3 stop_reason=end_turn
ASSISTANT: Three Eiffel Towers stacked would be 990 meters tall.
```

## How to run

```bash
# from repo root, with ANTHROPIC_API_KEY in .env
cd 01-agentic-architecture/01-agentic-loops
python agentic_loop.py
python agentic_loop.py "What is 2+2?"       # no tools needed -> end_turn on iteration 1
pytest -q                                   # offline tests, no key required
```

## Code walkthrough

### 1. Configuration

All behaviour-governing numbers live at the top with comments. `MAX_ITERATIONS = 20` is
documented as *safety cap only* so nobody "optimises" it into the exit condition. `max_tokens`
is per response, not per run.

### 2. `Tool` dataclass — schema + handler in one object

A tool is two halves: what Claude sees (`name`, `description`, `input_schema`) and what you run
(`handler`). Keeping them together prevents schema/function drift. `to_param()` strips the
handler before the schema is sent to the API. The `description` does most of the work in tool
*selection* — vague descriptions produce wrong tool choices.

### 3. `ToolRegistry.execute()` — errors are data, not exceptions

Unknown tool, bad arguments, or a tool that blows up all **return** `(error_text, True)` instead
of raising. The model sees the error as a `tool_result` with `is_error: true` and can recover
(retry with fixed input, pick another tool, explain the failure). Raising would kill the run.
Only *your* bugs (bad key, broken code) should crash the loop.

### 4. Calculator — AST walk, no `eval()`

Tool inputs are untrusted: a prompt can smuggle `__import__('os')...` into an expression. The
calculator parses to an AST and only evaluates numeric constants and arithmetic operators;
anything else raises, which the registry turns into an error result.

### 5. Search stub — JSON out, backend swappable

Returns `{"query": ..., "results": [...]}` with an explicit empty list when nothing is found,
so the model can distinguish "ran, found nothing" from "failed". Swap the body for a real
search API and nothing else in the file changes.

### 6. `AgentRun` — the result is more than the text

Returns `final_text`, `stop_reason`, `iterations`, `tool_calls`, token usage and the full
`messages` history. Tests assert on `run.tool_calls == 2`, not on regexes over prose.

### 7. `_call_model()` — one call, no custom retry

`system` is a top-level parameter (not a `role: "system"` message). The client was built with
`max_retries=3`, so the SDK already retries 429/5xx with backoff — a hand-written retry would
double-retry. Auth and bad-request errors propagate because they are setup bugs.

### 8. The loop — `stop_reason` is the only switch

```python
while True:
    iterations += 1
    if iterations > MAX_ITERATIONS: raise IterationCapExceeded(...)
    response = self._call_model(messages)
    if response.stop_reason == "tool_use":   ...; continue
    if response.stop_reason == "end_turn":   return ...
    if response.stop_reason == "max_tokens": return ...  # truncated, labelled as such
    if response.stop_reason == "refusal":    return ...
    raise RuntimeError(f"Unhandled stop_reason: {response.stop_reason!r}")
```

No `break` on content, no `"done" in text`, no "are there tool_use blocks?" check. The final
`raise` means a future new stop reason fails loudly instead of looping to the cap.

### 9. `tool_use` branch — three steps, strict order

1. Append the assistant message **unchanged** (`response.content`), so every `tool_use` id is in history.
2. Iterate over **all** `ToolUseBlock`s — Claude can request several tools in one turn. Each result carries the matching `tool_use_id`.
3. Append **one** `role: "user"` message containing all `tool_result` blocks. Then `continue`.

### 10. `end_turn` branch

Join all `TextBlock`s (never `content[0].text`) and return. This is the only normal exit.

### 11. `max_tokens` / `refusal`

Degraded exits, returned with the `stop_reason` so the caller knows the text is partial or empty.
A loop that only knows `tool_use` and `end_turn` will hit one of these and misbehave.

### 12. Safety cap

Checked at the **top** of the loop (before spending an API call), **raises** rather than returns,
and **logs a warning**. Hitting it means the model never said `end_turn` — that's an incident,
not a result. `main()` maps it to exit code 2.

### 13. `main()` — distinct exit codes

Cap hit → 2, bad key → 3, other API error → 4. A pipeline can tell them apart without parsing logs.

## Key takeaways

1. **`stop_reason` drives the loop. Nothing else.** It is deterministic and set by the API.
2. **`tool_use` →** append assistant turn verbatim → run *every* tool block → one user message of `tool_result`s with matching `tool_use_id`s → `continue`.
3. **`end_turn` →** join text blocks → return.
4. **Tool errors are results** (`is_error: true`), so the model can recover. Only your own bugs should raise.
5. **The iteration cap is a tripwire, not the exit.** It should never fire in normal operation; if it does, find the bug.
6. The model is stateless — the full `messages` list is resent every call, so input tokens grow each iteration.
7. Tool `description` is what Claude reads to pick a tool; a short system prompt ("use calculator for ALL math") firms up selection.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| `if any(b.type == "tool_use" for b in content)` to decide whether to loop | Content-type checks are not the API's signal; `stop_reason` is. |
| Parsing the reply for "done" / "finished" | Claude says "I'm finished searching, now calculating" → premature exit. |
| `for i in range(5): ...` as the loop | The cap becomes the exit; you never know if the answer was complete. |
| `response.content[0]` | Misses multiple text blocks and parallel tool calls. |
| Sending `tool_result` as `role: "assistant"` | Rejected by the API; tool results are the user's turn. |
| One message per tool result | Must be a single user message containing all results. |
| Filtering the assistant message to only tool_use blocks before appending | Breaks `tool_use_id` matching on the next call. |
| Raising on tool failure | Kills a run the model could have recovered. |
| `role: "system"` inside `messages` | `system` is a top-level parameter of `messages.create`. |

## Stop reasons reference

| Value | Meaning | What the loop does |
|---|---|---|
| `tool_use` | Claude wants one or more tools run | Execute, append results, continue |
| `end_turn` | Claude finished | Return final text |
| `max_tokens` | Output truncated at `max_tokens` | Return partial, labelled |
| `refusal` | Claude declined | Return empty, labelled |
| `stop_sequence` | Hit a custom stop string | Not used here; handled by the final `raise` |
