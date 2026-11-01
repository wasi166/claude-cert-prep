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
| 1 | Set up a Claude API client with two tools: a **calculator** (accepts `expression`, returns result) and a **web search stub** (accepts `query`, returns mock results). | Multi-tool setups expose model-driven decision-making — Claude must pick the right tool from context, which is core to agentic architecture. | Two tool definitions with proper JSON Schema `input_schema`, each with `name`, `description`, and parameters. | `TOOLS`, `calculator()`, `web_search()` |
| 2 | Implement the agentic loop that sends requests to Claude and **inspects `stop_reason`** after each response. | The exam tests whether you use `stop_reason` (deterministic) versus content-type checks or natural-language parsing (unreliable). | A `while` loop that calls `client.messages.create()` and checks `response.stop_reason` each iteration. | `run_agent()` |
| 3 | Handle `tool_use`: execute the requested tool, create a tool result message, append it to history. | The critical step — the exam checks that you extract tool calls, execute them, and return results in the right message format. | On a tool request, the code extracts the `tool_use` block, runs the matching function, and appends both the assistant response and a user message containing `tool_result`. | `tool_use` branch in `run_agent()` |
| 4 | Handle `end_turn`: extract and return the final response. | `end_turn` is Claude's signal that the task is complete; extracting the text correctly ends the loop. | When `stop_reason` is `end_turn`, the loop exits and returns the text content. | `end_turn` branch in `run_agent()` |
| 5 | Test with a prompt that needs **multiple sequential tool calls** (search for a value, then calculate with it) and verify the loop continues through all iterations. | Sequential calls test the full lifecycle: complete one tool call, receive the result, reason about it, decide to call another, then return. | At least two tool-call iterations before `end_turn`: search first, use the result in a calculation, return the combined answer. | `prompt` under `if __name__ == "__main__"` |
| 6 | Add a **safety iteration cap of 20** as a maximum bound (not the primary stopping mechanism) and log a warning if it triggers. | The exam distinguishes safety caps (acceptable fallback) from caps as the primary stop (anti-pattern). The cap should never trigger in normal operation. | A `MAX_ITERATIONS` constant, a counter incremented each loop, and a warning log if the cap is hit. | `MAX_ITERATIONS`, top of the `while` loop |

## What this builds

`agentic_loop.py` is ~150 lines, and most of them are comments. Claude gets two tools (a
calculator and a stubbed web search). A `while` loop calls the Messages API, runs whatever tool
Claude asks for, sends the result back, and repeats — until `stop_reason` says `end_turn`.
The test prompt forces two *sequential* tool calls so you can watch the whole lifecycle:

```
USER: Find the height of the Eiffel Tower, then calculate how tall three of them stacked ... would be.

[iteration 1] stop_reason = tool_use
    web_search({'query': 'Eiffel Tower height'}) -> The Eiffel Tower is 330 meters tall.
[iteration 2] stop_reason = tool_use
    calculator({'expression': '330 * 3'}) -> 990
[iteration 3] stop_reason = end_turn

CLAUDE: Three Eiffel Towers stacked would be 990 meters tall.
```

## Files

| File | Purpose |
|---|---|
| `agentic_loop.py` | **The lesson.** Read this first. |
| `test_agentic_loop.py` | Offline tests for the three core ideas (tool_use → end_turn → cap). Run with `pytest -q`. |
| `hardened_version.py` | *Optional.* The same loop with production concerns added (error handling, safe eval, retries, exit codes). Read after the lesson to see what changes and, more importantly, what doesn't. |

## How to run

```bash
# from repo root, with ANTHROPIC_API_KEY in .env
cd 01-agentic-architecture/01-agentic-loops
python agentic_loop.py      # runs the two-tool prompt above
pytest -q                   # offline tests, no key required
```

Try editing the `prompt` at the bottom to `"What is 2 + 2?"` and run again. You should see
`end_turn` on iteration 1 with no tool calls — Claude, not your code, decided no tool was needed.

## Code walkthrough

### Step 1 — Two tool definitions

```python
TOOLS = [
    {"name": "calculator", "description": "...", "input_schema": {...}},
    {"name": "web_search", "description": "...", "input_schema": {...}},
]
```

Each tool is a dict with exactly three keys: `name`, `description`, and `input_schema` (plain
JSON Schema). That's the entire contract with the API. **The `description` is how Claude decides
which tool to use** — it reads them like documentation. "Use this for any math" is doing real
work in the calculator's description.

Below the definitions sit two ordinary Python functions and a tiny `run_tool()` dispatcher.
This separation is the key mental model: **the API never executes anything.** It returns a
message saying "I'd like `calculator` called with `{"expression": "330 * 3"}`". Running the
function is your job.

### Step 2 — Call Claude, read `stop_reason`

```python
response = client.messages.create(model=MODEL, max_tokens=1024, tools=TOOLS, messages=messages)
print(f"[iteration {iteration}] stop_reason = {response.stop_reason}")
```

One call per iteration, passing the *whole* `messages` list every time (the model is stateless).
Then the only thing we look at to decide what to do next is `response.stop_reason`. Not the
text, not whether there happen to be `tool_use` blocks in the content — just that one field.

### Step 4 — `end_turn`: Claude is finished

```python
if response.stop_reason == "end_turn":
    return "".join(block.text for block in response.content if block.type == "text")
```

(Listed before step 3 in the code because it's the simplest branch.) `content` is a list of
blocks; join all the text blocks. This `return` is the *only* normal way out of the loop.

### Step 3 — `tool_use`: three moves, in order

```python
if response.stop_reason == "tool_use":
    messages.append({"role": "assistant", "content": response.content})          # (a)

    tool_results = []
    for block in response.content:                                               # (b)
        if block.type == "tool_use":
            result = run_tool(block.name, block.input)
            tool_results.append({"type": "tool_result",
                                 "tool_use_id": block.id,
                                 "content": result})

    messages.append({"role": "user", "content": tool_results})                   # (c)
    continue
```

**(a)** Append Claude's turn to history *exactly as it came back*. It contains the `tool_use`
block(s) with their `id`s, and the API will check on the next call that every result you send
matches one of those ids.

**(b)** Loop over *all* blocks, not just the first. Claude can ask for several tools in one turn.
Each result is tagged with the `id` of the block it answers — that's how the model pairs
questions with answers.

**(c)** All results go back as **one message with `role: "user"`**. Tool results are the user's
turn structurally: you're telling Claude what happened in the world. Then `continue` — Claude
needs to see the results and decide whether it's done or needs another tool.

### Step 6 — The safety cap

```python
iteration += 1
if iteration > MAX_ITERATIONS:
    print("WARNING: hit MAX_ITERATIONS ...")
    return "Agent stopped: iteration cap reached."
```

Sits at the top of the `while True` so it's checked before spending an API call. It prints a
warning because reaching it means something is wrong — Claude never said `end_turn`. In a healthy
run this code is never reached; our test prompt finishes on iteration 3.

Notice the loop is `while True`, not `for i in range(20)`. Same behaviour, different message:
`while True` says "I'll run until Claude is done", and the cap is visibly a guard. A `for` loop
says "I'll run 20 times", which is the anti-pattern the exam is looking for.

### The fall-through

```python
return f"Stopped with unexpected stop_reason: {response.stop_reason}"
```

If `stop_reason` is anything else (`max_tokens`, `refusal`, `stop_sequence`), we stop and say
so. The alternative — silently looping until the cap — hides the real problem.

### Step 5 — The test prompt

A prompt Claude can't answer in one shot: it has to search (tool 1), read the result, then
calculate (tool 2), then answer. The three `[iteration N]` lines in the output are the proof
that history is being built correctly — if step 3 were wrong, iteration 2 would fail with an
API error about unmatched `tool_use_id`s.

## Key takeaways

1. **`stop_reason` drives the loop. Nothing else.** It is deterministic and set by the API.
2. **`tool_use` →** append assistant turn verbatim → run *every* tool block → one user message of `tool_result`s with matching `tool_use_id`s → `continue`.
3. **`end_turn` →** join text blocks → return.
4. **The API never runs code.** It asks for a tool by name; your code runs it and reports back.
5. **The iteration cap is a tripwire, not the exit.** It should never fire in normal operation; if it does, find the bug.
6. **The model is stateless.** The full `messages` list is resent every call — that's what lets Claude "remember" the search result when it calculates.
7. **Tool `description` is how Claude picks a tool.** Write it like documentation for a colleague.
8. Tool errors should go back as results (the calculator returns `"error: ..."` strings), so the model can recover. `hardened_version.py` goes further with `is_error: true`.

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
