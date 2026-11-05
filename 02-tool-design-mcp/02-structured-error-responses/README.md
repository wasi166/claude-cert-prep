# 2.2 — Structured Error Responses

**Domain 2:** Tool Design & MCP Integration (18%) · **Concepts:** `isError`, `errorCategory`, `isRetryable`, valid-empty vs access-failure, category→action recovery, exponential backoff

## Learning objectives

From the curriculum, after this lesson you should be able to:

- Implement structured error responses with `errorCategory`, `isRetryable`, and `description` metadata
- Distinguish between access failures (`isError: true`) and valid empty results (`isError: false`)
- Categorise tool failures into transient, validation, business, and permission types
- Build agent recovery logic that takes different actions based on error metadata

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | An MCP tool querying a **mock customer database with simulated failure modes**. | Controlled failures let you observe agent behaviour when errors lack structure. Poor error responses cause wasted retries and wrong escalations. | A `customer_lookup` tool taking an identifier and a `failure_mode` parameter. | `customer_lookup()`, `CUSTOMER_LOOKUP_TOOL` |
| 2 | **Four error types**: transient (timeout), validation (bad format), business (refund over limit), permission (access denied). | Each category needs a different recovery. Transient is retryable; business never is. | Four distinct responses, each `isError: true` with a category, the right `isRetryable`, and a descriptive message. | the four branches in `customer_lookup()` |
| 3 | **Structured metadata** in each error: `errorCategory`, `isRetryable`, `description`. | Metadata enables intelligent recovery. `isRetryable: false` means take an alternative path, not retry. | Each error parses to exactly three fields. | `_error()` |
| 4 | A **valid empty result** (`isError: false`, `resultCount: 0`) distinguished from an access failure. | The most critical Domain 2 distinction. Retrying a successful empty query is the canonical anti-pattern. | Two structurally different responses. | the `record is None` branch; `test_valid_empty_is_structurally_different_from_an_error` |
| 5 | An **agent loop** branching on category: retry transient (≤3, backoff), fix input for validation, escalate business, request credentials for permission. | Each category maps to one action; the loop must branch correctly. | A loop that parses metadata and takes the mapped action. | `handle_with_recovery()` |

## What this builds

```
customer_lookup(...) ──► one of three shapes:

   SUCCESS       {"isError": false, content: {"resultCount": 1, "results": [...]}}
   VALID EMPTY   {"isError": false, content: {"resultCount": 0, "results": []}}       ← done; report it
   ERROR         {"isError": true,  content: {"errorCategory", "isRetryable", "description"}}
                                                    │
                     ┌──────────────┬───────────────┼───────────────┬──────────────┐
                     ▼              ▼               ▼               ▼              ▼
                transient      validation       business       permission      (unknown)
              retry w/ backoff  fix input       escalate       get creds       escalate
               (max 3)          call again      never retry    call again
```

The deterministic run (`python structured_errors.py`) walks six scenarios and prints the action
trail for each:

```
success      -> success
valid empty  -> report_not_found
transient    -> retry_1_after_0.5s -> retry_2_after_1s -> success
validation   -> reformat_input:889->CUST-0889 -> success
business     -> escalate_business
permission   -> request_credentials -> success
```

Each trail is also what the tests assert on. `--live` runs the same six scenarios with Claude
reading the structured results and choosing the action under a short policy prompt.

## Files

| File | Purpose |
|---|---|
| `structured_errors.py` | **The lesson.** The tool with four failure categories and a valid-empty path; the recovery loop; a live Claude variant. |
| `test_structured_errors.py` | Offline tests: every category's shape and retryability; valid-empty vs error; every recovery branch including backoff timing, retry cap, and the two anti-patterns. |

## How to run

```bash
cd 02-tool-design-mcp/02-structured-error-responses
python structured_errors.py          # offline, deterministic
python structured_errors.py --live   # ~15 API calls
pytest -q
```

## Code walkthrough

### The result shape — MCP's `CallToolResult`

```python
def _ok(payload):     return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
def _error(cat, retryable, description):
    body = {"errorCategory": cat, "isRetryable": retryable, "description": description}
    return {"content": [{"type": "text", "text": json.dumps(body)}], "isError": True}
```

MCP tools return `content` plus an `isError` flag. That flag is the first fork in the road: the
caller checks it before reading anything else. In the Messages API the same bit becomes
`tool_result.is_error`; `run_live()` copies it across directly, never inferring it from text.

`_error()` is the only way to build an error, and it builds exactly three fields. The test
asserts `set(body) == {"errorCategory", "isRetryable", "description"}` for every category.

### Steps 2–3 — The four categories

```python
if failure_mode == "access_denied" and not elevated:
    return _error("permission", False, "Access denied: ... Request elevated credentials, then call again with elevated=true.")
if failure_mode == "timeout":
    ...; return _error("transient", True, "Upstream customer database timed out ... Safe to retry with backoff.")
if not ID_FORMAT.match(identifier):
    return _error("validation", False, "Invalid identifier '889': expected format CUST-#### ... retrying unchanged will fail identically.")
...
if refund_amount > record["refund_limit"]:
    return _error("business", False, "Refund of $750.00 exceeds the $100 policy limit ... escalate to a human for approval.")
```

| Category | Cause | `isRetryable` | Why |
|---|---|---|---|
| transient | the world hiccupped (timeout, 503) | **true** | the same call may succeed later |
| validation | *your* input is malformed | false | the same input fails the same way; change it |
| business | the request violates policy | false | policy doesn't change on retry; a human must decide |
| permission | you lack rights | false | retrying unauthenticated is pointless; get credentials |

Only transient is retryable. The other three all require *doing something different* before
calling again — which is not a retry. Every `description` says what went wrong **and** what to
do next; the loop never has to parse it, but a model (or a human reading logs) can.

### Step 4 — Valid empty

```python
record = CUSTOMERS.get(identifier)
if record is None:
    return _ok({"resultCount": 0, "results": [], "note": "... The query succeeded; the record does not exist."})
```

`isError: false`. `resultCount: 0`. No `errorCategory`. It is a *success* whose answer is
"nothing". The loop's first check handles it:

```python
if not result["isError"]:
    if body["resultCount"] == 0: return "No customer found ...", ["report_not_found"]
```

One call, one action, done. The test `test_valid_empty_is_reported_not_retried` pins
`actions == ["report_not_found"]`. If a system treats "no results" as an error — even a
retryable one — it will hammer a database that has already answered correctly, and may escalate
a non-problem to a human. That's the anti-pattern the exam targets.

### Step 5 — The recovery loop

```python
if cat == "transient" and retryable:
    if retries >= MAX_RETRIES: return escalate(...), actions
    delay = BACKOFF_BASE * (2 ** retries); retries += 1; sleep(delay); continue
if cat == "validation":
    fixed = reformat_identifier(identifier)
    if fixed is None or fixed == identifier: return escalate(...), actions
    identifier = fixed; continue
if cat == "business":
    return escalate(body["description"]), actions          # never retried
if cat == "permission":
    if elevated: return escalate(...), actions
    elevated = acquire_elevated_credentials(); continue
```

Four things to notice:

- **It branches on `errorCategory`**, a string from a closed set. Never on `"timeout" in description`.
- **Backoff is exponential**: 0.5s, 1s, 2s. `sleep` is injected so tests can capture the delays
  (`assert delays == [0.5, 1.0]`) without waiting.
- **Validation's `continue` is not a retry.** The identifier changed. If it *can't* be fixed, or
  fixing produced the same string, the loop escalates instead of looping.
- **Business returns on the first error.** The test wraps `customer_lookup` in a counter and
  asserts exactly one call was made.

The `--live` variant hands the same tool and a six-line policy to Claude. The policy restates
the category→action table; the structure in the tool result is what makes it followable.

## Key takeaways

1. **Three shapes, checked in order:** `isError` first; then `resultCount`; then `errorCategory`. Never read prose to decide.
2. **Exactly three fields in an error:** `errorCategory` (closed enum), `isRetryable` (bool), `description` (what happened + what to do).
3. **Only transient is retryable.** Validation, business, and permission each need a *different* next call, or a human.
4. **Valid empty is a success.** `isError: false, resultCount: 0`. Report it; never retry it; never escalate it.
5. **`isRetryable: false` means change course**, not "retry fewer times".
6. **Retry with exponential backoff and a cap.** Then escalate — a transient failure that persists is no longer transient.
7. **Fixing input then calling again is not a retry.** Different input, different call. Track it as such.
8. **The description is for humans and models; the metadata is for code.** Both matter; only the metadata drives control flow.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| Retrying a `resultCount: 0` result | It succeeded. You're hammering a correct answer and may escalate a non-issue. |
| Returning "no results" as `isError: true` | Forces every caller to special-case it; conflates absence with failure. |
| Retrying a `business` error | Policy doesn't change between attempts. Three identical refusals, then an escalation that should have happened immediately. |
| Retrying a `validation` error unchanged | Same input, same failure. Fix the input first. |
| Retrying a `permission` error without new credentials | Same rights, same denial. |
| Branching on description text (`"timeout" in msg`) | Brittle. Wording changes break recovery; the category enum doesn't. |
| Error payloads with extra, inconsistent fields | Callers can't rely on the shape. Three fields, always. |
| Unbounded transient retries | A persistent "transient" failure is an outage. Cap, then escalate. |
| Constant retry delay | Hammers a struggling backend. Each delay should be longer than the last. |
| Inferring `is_error` from the text when forwarding to the model | The flag exists; copy it. |

## Category → action reference

| `errorCategory` | `isRetryable` | Agent action | Then |
|---|---|---|---|
| transient | true | wait `base × 2^n`, same call | up to 3×, then escalate |
| validation | false | reformat input | call again once; escalate if unfixable |
| business | false | escalate to human | stop |
| permission | false | request elevated credentials | call again with `elevated=true`; escalate if still denied |
| *(valid empty)* | — | report "not found" | stop |
