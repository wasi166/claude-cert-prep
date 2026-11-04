# 2.1 — Tool Interface Design

**Domain 2:** Tool Design & MCP Integration (18%) · **Concepts:** descriptions as the routing mechanism, the five elements of a production description, misrouting diagnosis, system-prompt conflicts

## Learning objectives

From the curriculum, after this lesson you should be able to:

- Understand that tool descriptions are the primary mechanism LLMs use for tool selection
- Write production-grade descriptions with purpose, inputs, examples, edge cases, and boundaries
- Diagnose misrouting caused by ambiguous or overlapping descriptions
- Identify system prompt conflicts that override well-written tool descriptions

## Problem statement

| # | Requirement | Why it matters | What you should see | Where in the code |
|---|---|---|---|---|
| 1 | Two MCP tools with **intentionally ambiguous descriptions** (`get_customer`: "Retrieves customer information", `lookup_order`: "Retrieves order details"). | Reproducing misrouting yourself shows why minimal descriptions fail. The exam tests identifying ambiguity as the root cause. | Two single-sentence descriptions with no input formats, examples, or boundaries. | `VAGUE_TOOLS` |
| 2 | **Test with 10 queries** covering different intents; log which tool is selected. | Quantifying accuracy before/after gives concrete evidence. Description quality directly affects selection reliability. | 2–3 misrouted queries: `get_customer` for order questions or vice versa. | `QUERIES`, `evaluate()` — run 1 |
| 3 | **Rewrite both descriptions** with purpose, inputs + formats, example queries, edge cases, explicit boundaries. | The lowest-effort, largest-effect fix. Production descriptions have all five elements. | 3–5 sentences each, identifier formats stated, examples given, a "Do NOT use for … — use X" boundary. | `GOOD_TOOLS`, `check_description()` |
| 4 | **Re-run the 10 queries**; compare before/after. | Validates descriptions as the root cause: measurably better selection with no architectural change. | 9/10 or 10/10, previously misrouted queries now correct. | `evaluate()` — run 2, SUMMARY |
| 5 | **Review the system prompt** for keyword-sensitive instructions that override descriptions. | A subtle failure mode: "always check customer details" creates a tool association that beats a good description. | A list of conflicting phrases with rewritten versions. | `CONFLICTING_PROMPT`, `NEUTRAL_PROMPT`, `audit_system_prompt()` — runs 3 & 4 |

## What this builds

Same two tools, same schemas, same 10 queries — run four times:

| Run | Descriptions | System prompt | Shows |
|---|---|---|---|
| 1 | vague | none | baseline misrouting |
| 2 | good | none | the fix |
| 3 | good | conflicting ("Always check customer details first…") | a prompt overriding good descriptions |
| 4 | good | neutral | conflict removed |

`tool_choice={"type": "any"}` forces exactly one tool call per query, so each run produces ten
clean selections to grade. We're measuring *which* tool, not *whether* a tool.

Expected output shape:

```
RUN 1 — vague descriptions, no system prompt
  OK   lookup_order   What's the status of order ORD-4471?
  MIS  get_customer   Which customer placed order 4471?
  MIS  lookup_order   How many orders has CUST-889 placed in total?
  MIS  get_customer   Did the customer's order ORD-4471 include the blue jacket?
  ...
  accuracy: 7/10

RUN 2 — good descriptions, no system prompt
  accuracy: 10/10

System prompt audit:
  CONFLICTING: 2 keyword-sensitive phrase(s)
    - "Always check customer details first before doing anything else."
      -> 'always ... customer' creates a blanket association with get_customer
    - "Customer context is essential — look up the customer whenever a request comes in."
      -> 'whenever ...' turns a tool into a reflex instead of a choice
  NEUTRAL: 0 keyword-sensitive phrase(s)

RUN 3 — good descriptions + CONFLICTING system prompt    accuracy: 6/10
RUN 4 — good descriptions + NEUTRAL system prompt        accuracy: 10/10
```

Exact counts vary by run. The pattern — vague < good, and conflicting-prompt < neutral-prompt
with identical descriptions — is the lesson.

## Files

| File | Purpose |
|---|---|
| `tool_descriptions.py` | **The lesson.** Vague and good tool sets, 10 queries, the four runs, the description checker, the system-prompt audit. |
| `test_tool_descriptions.py` | Offline tests: five-element check; schemas identical across versions; balanced queries with distractors; audit flags/passes; harness forces a tool call and scores selection. |

## How to run

```bash
cd 02-tool-design-mcp/01-tool-interface-design
python tool_descriptions.py     # 40 API calls (4 runs x 10 queries), small max_tokens
pytest -q
```

## Code walkthrough

### Step 1 — Vague descriptions

```python
{"name": "get_customer", "description": "Retrieves customer information.", ...}
{"name": "lookup_order", "description": "Retrieves order details.", ...}
```

Both are true. Neither helps with "Which customer placed order 4471?" — the query mentions a
customer, so a model keying on nouns picks `get_customer`; but the only identifier available is
an order ID, so the right call is `lookup_order`. Nothing in either description says so.

### Step 2 — The query set

Ten queries, five per tool, and several built to mislead a keyword reader:

| Query | Mentions | Correct tool | Why |
|---|---|---|---|
| Which customer placed order 4471? | customer | `lookup_order` | the input is an order ID |
| How many orders has CUST-889 placed? | orders | `get_customer` | the input is a customer ID; order *history* is account data |
| Did the customer's order ORD-4471 include the blue jacket? | customer | `lookup_order` | "customer's" is possessive noise |

The distractors are where vague descriptions fail and good ones succeed. Routing by **what
identifier the user gave** rather than **which nouns appear** is the skill the descriptions
have to teach the model.

### Step 3 — The five elements

```python
"description": (
    # purpose
    "Look up a customer's account profile: name, contact details, membership tier, ... order history (count and dates). "
    # inputs + formats
    "Accepts a customer ID in the form CUST-#### (e.g. CUST-889) or the customer's email address. "
    # examples
    "Use for queries like 'is jane@example.com a premium member?', 'how many orders has CUST-889 placed?' ... "
    # edge cases
    "If the user gives a name only, ask for an email or customer ID rather than guessing. "
    # boundary
    "Do NOT use for anything about a specific order (status, tracking, items, cancellation, refunds) — "
    "use lookup_order for those, even when the customer is also mentioned."
)
```

Each element answers a question the model would otherwise guess at:

| Element | Answers | Without it |
|---|---|---|
| Purpose | what comes back | model infers scope from the name |
| Inputs + formats | what identifiers to look for in the query | `CUST-889` vs `ORD-4471` is the strongest routing signal; unstated, it's unused |
| Examples | what real queries look like | abstract purpose is harder to match than concrete phrasings |
| Edge cases | what to do when the input is missing or odd | model guesses, often by calling the wrong tool with a made-up ID |
| Boundary | which tool *not* to use, and for what | overlap is resolved by noun-matching — the failure mode |

The boundary is the one most people omit and the one with the biggest effect on the
distractors. Note it names the other tool and the exact cases ("even when the customer is also
mentioned").

`check_description()` verifies all five are present. It's a heuristic, not a judge of prose —
but it fails the vague descriptions 0/5 and passes the good ones 5/5, which is the bar.

### Step 4 — Same queries, same schemas, new descriptions

`VAGUE_TOOLS` and `GOOD_TOOLS` share `CUSTOMER_SCHEMA` and `ORDER_SCHEMA` by reference, and a
test asserts it. The description is the only thing that changes between run 1 and run 2, so
any accuracy difference is attributable to it.

### Step 5 — System prompt conflicts

```python
CONFLICTING_PROMPT = ("Always check customer details first before doing anything else. "
                      "Customer context is essential — look up the customer whenever a request comes in.")
```

Reads like reasonable support-desk guidance. In practice it tells the model to call
`get_customer` for every query — including "cancel order ORD-4471" — because the system prompt
is read as policy and the tool description as reference. Run 3 shows good descriptions losing
to a bad prompt; run 4 shows the same descriptions winning once the conflict is removed.

`audit_system_prompt()` is a small regex lint for the usual culprits: `always … customer`,
`first …`, `whenever … look up`, `never <tool>`, `<tool> is the default`. The rewrite principle:
the system prompt sets *role and tone*; the descriptions do *routing*. A prompt that names tools
or sequences is doing the descriptions' job, badly.

```python
NEUTRAL_PROMPT = ("You are a support assistant. Choose the tool whose description matches the user's "
                  "request. Each tool's description states what it is for and when to use the other one.")
```

## Key takeaways

1. **The description is the router.** The model has no other information about when to use a tool. A one-line description is a one-line routing rule.
2. **Five elements, every time:** purpose · inputs with formats · example queries · edge cases · boundary against the overlapping tool.
3. **Identifier formats are the strongest signal.** `CUST-####` vs `ORD-####` resolves most ambiguity — if the description says so.
4. **Boundaries beat nouns.** "Do NOT use for order-specific queries — use lookup_order, even when the customer is mentioned" is what fixes the distractors.
5. **Measure selection, not vibes.** Fixed query set, forced tool choice, before/after accuracy. Same model, same schemas; only the text changed.
6. **The system prompt can override everything.** "Always check customer details first" turns a tool into a reflex. Audit prompts for `always / first / whenever / never / default` near tool concepts.
7. **Prompt sets role; descriptions do routing.** Keep sequencing and tool names out of the system prompt.
8. **MCP changes nothing here.** An MCP tool is `name` + `description` + `inputSchema`; the model reads it the same way.

## Exam traps

| Anti-pattern | Why it fails |
|---|---|
| One-sentence descriptions ("Retrieves customer information.") | No formats, no examples, no boundary — selection falls back to noun-matching. |
| Two tools whose descriptions overlap without a boundary | Any query mentioning both concepts is a coin flip. |
| Describing what the tool *does* but not what it *accepts* | The identifier in the query is the best routing clue, and it's unused. |
| Fixing misrouting with architecture (a router agent, more tools) | Expensive and unnecessary when the description is the root cause. Fix the text first. |
| Fixing misrouting by changing the schema | Schemas are validated after selection. The pick is made from the description. |
| "Always check customer details first" in the system prompt | Overrides good descriptions; every query routes to `get_customer`. |
| Naming tools or sequences in the system prompt | Duplicates routing logic in a place that outranks the descriptions. |
| Judging accuracy from a few manual tries | Use a fixed query set with known answers and a forced tool choice. |

## Description template

```
<Purpose: what it returns, in one sentence.>
Accepts <identifier> in the form <FORMAT> (e.g. <EXAMPLE>) or <alternative>.
Use for queries like '<q1>', '<q2>', or '<q3>'.
If <edge case>, <what to do> rather than <what not to do>.
Do NOT use for <the other tool's domain> — use <other_tool> for those, even when <distractor>.
```
