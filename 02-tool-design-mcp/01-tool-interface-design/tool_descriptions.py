"""
Lesson 2.1 — Tool Interface Design: descriptions that eliminate misrouting

The model picks a tool by reading its description. Nothing else. A description is a routing
rule written in prose, and a vague one routes wrongly.

    VAGUE:  "Retrieves customer information."          -> which tool for "who placed order 4471"?
    GOOD:   purpose + inputs & formats + example queries + edge cases + boundary vs the other tool

Four runs over the same 10 queries:
    1. vague descriptions                      (baseline misrouting)
    2. good descriptions                       (the fix)
    3. good descriptions + conflicting prompt  (a system prompt that overrides them)
    4. good descriptions + neutral prompt      (the conflict removed)

tool_choice={"type": "any"} forces exactly one tool call per query so we grade SELECTION only.

Run:  python tool_descriptions.py        (needs ANTHROPIC_API_KEY in the repo-root .env)
"""

import os
import re

import anthropic
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-sonnet-4-5"

# ---------------------------------------------------------------------------
# Input schemas are identical in both versions. Only the description changes.
# (An MCP server exposes exactly these three fields — name, description, inputSchema —
#  so everything here applies unchanged to MCP tools.)
# ---------------------------------------------------------------------------
CUSTOMER_SCHEMA = {
    "type": "object",
    "properties": {"identifier": {"type": "string", "description": "Customer ID or email address."}},
    "required": ["identifier"],
}
ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string", "description": "Order ID."}},
    "required": ["order_id"],
}

# ---------------------------------------------------------------------------
# Step 1: intentionally ambiguous descriptions
# ---------------------------------------------------------------------------
VAGUE_TOOLS = [
    {"name": "get_customer", "description": "Retrieves customer information.", "input_schema": CUSTOMER_SCHEMA},
    {"name": "lookup_order", "description": "Retrieves order details.", "input_schema": ORDER_SCHEMA},
]

# ---------------------------------------------------------------------------
# Step 3: production-grade descriptions. Five elements each:
#   purpose · inputs with formats · example queries · edge cases · boundary vs the other tool
# ---------------------------------------------------------------------------
GOOD_TOOLS = [
    {
        "name": "get_customer",
        "description": (
            # purpose
            "Look up a customer's account profile: name, contact details, membership tier, "
            "account status, saved addresses, and a summary of their order history (count and dates). "
            # inputs + formats
            "Accepts a customer ID in the form CUST-#### (e.g. CUST-889) or the customer's email address. "
            # examples
            "Use for queries like 'is jane@example.com a premium member?', 'how many orders has CUST-889 placed?', "
            "or 'what phone number is on file for this customer?'. "
            # edge cases
            "If the user gives a name only, ask for an email or customer ID rather than guessing. "
            # boundary
            "Do NOT use for anything about a specific order (status, tracking, items, cancellation, refunds) — "
            "use lookup_order for those, even when the customer is also mentioned."
        ),
        "input_schema": CUSTOMER_SCHEMA,
    },
    {
        "name": "lookup_order",
        "description": (
            # purpose
            "Fetch a single order's record: items, amounts, payment status, fulfilment status, tracking "
            "number, delivery estimate, and the customer ID that placed it. "
            # inputs + formats
            "Accepts an order ID in the form ORD-#### (e.g. ORD-4471); a bare 4-digit number from the user "
            "should be treated as ORD-####. "
            # examples
            "Use for queries like 'where is order 4471?', 'did ORD-4471 ship?', 'cancel order 4471', "
            "or 'which customer placed ORD-4471?'. "
            # edge cases
            "If no order ID is given, ask for one rather than searching by customer. "
            # boundary
            "Do NOT use for questions about the customer's account, membership, contact details, or overall "
            "order history — use get_customer for those."
        ),
        "input_schema": ORDER_SCHEMA,
    },
]

# ---------------------------------------------------------------------------
# Step 2: ten queries, five per intent, several deliberately mentioning both concepts
# ---------------------------------------------------------------------------
QUERIES = [
    ("What's the status of order ORD-4471?",                                   "lookup_order"),
    ("Is jane@example.com a premium member?",                                  "get_customer"),
    ("Which customer placed order 4471?",                                      "lookup_order"),   # mentions customer; input is an order
    ("How many orders has CUST-889 placed in total?",                          "get_customer"),   # mentions orders; input is a customer
    ("Cancel order ORD-4471 for me.",                                          "lookup_order"),
    ("Update the shipping address on jane@example.com's account.",            "get_customer"),
    ("When will order 4471 be delivered?",                                     "lookup_order"),
    ("What phone number do we have on file for CUST-889?",                     "get_customer"),
    ("Did the customer's order ORD-4471 include the blue jacket?",             "lookup_order"),   # 'customer' is a distractor
    ("Show me the account details for the customer with email ali@example.com.", "get_customer"),
]

# ---------------------------------------------------------------------------
# Step 5: system prompts — one that conflicts with the descriptions, one that doesn't
# ---------------------------------------------------------------------------
CONFLICTING_PROMPT = (
    "You are a support assistant. Always check customer details first before doing anything else. "
    "Customer context is essential — look up the customer whenever a request comes in."
)
NEUTRAL_PROMPT = (
    "You are a support assistant. Choose the tool whose description matches the user's request. "
    "Each tool's description states what it is for and when to use the other one."
)

CONFLICT_PATTERNS = [
    # (regex, why it's a problem)
    (r"\balways\b.*\b(check|look ?up|verify|get|fetch)\b.*\bcustomer", "'always ... customer' creates a blanket association with get_customer"),
    (r"\b(first|before anything)\b.*\b(customer|order)\b", "'first ...' imposes a fixed sequence that overrides per-query routing"),
    (r"\b(whenever|every time|for every request)\b.*\b(look ?up|check)\b|\b(look ?up|check)\b.*\b(whenever|every time|for every request)\b", "'whenever ...' turns a tool into a reflex instead of a choice"),
    (r"\bnever\b.*\b(get_customer|lookup_order)\b", "'never <tool>' suppresses a tool regardless of the query"),
    (r"\b(get_customer|lookup_order)\b.*\b(is|are) (the )?(default|primary|main)\b", "naming a default tool biases every ambiguous query toward it"),
]


def audit_system_prompt(prompt: str) -> list[dict]:
    """Return keyword-sensitive phrases that could override tool descriptions."""
    hits = []
    for sentence in re.split(r"(?<=[.!?])\s+", prompt):
        for rx, why in CONFLICT_PATTERNS:
            if re.search(rx, sentence, flags=re.I):
                hits.append({"phrase": sentence.strip(), "problem": why})
                break
    return hits


# ---------------------------------------------------------------------------
# Description quality check — are the five elements present?
# ---------------------------------------------------------------------------
def check_description(desc: str, other_tool: str) -> dict[str, bool]:
    return {
        "purpose":   len(desc.split(".")[0].split()) >= 6,                       # a real first sentence
        "inputs":    bool(re.search(r"(CUST|ORD)-#{3,}|e\.g\.|\bin the form\b|\bformat\b", desc)),
        "examples":  bool(re.search(r"queries like|for example|such as|'[^']+\?'", desc)),
        "edge_cases": bool(re.search(r"\bIf\b.*\b(ask|treat|rather than)\b", desc)),
        "boundary":  other_tool in desc and bool(re.search(r"do not use|don't use|not for", desc, flags=re.I)),
    }


# ---------------------------------------------------------------------------
# Step 2 & 4: run the 10 queries, log the selection, score it
# ---------------------------------------------------------------------------
def select_tool(query: str, tools: list[dict], system: str | None) -> str:
    kwargs = dict(model=MODEL, max_tokens=200, tools=tools, messages=[{"role": "user", "content": query}],
                  tool_choice={"type": "any"})       # must pick one — we are grading the pick
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    return next(b.name for b in response.content if b.type == "tool_use")


def evaluate(tools: list[dict], system: str | None, label: str) -> tuple[int, list[tuple[str, str, str]]]:
    print(f"\n{label}")
    print("-" * len(label))
    rows, correct = [], 0
    for query, expected in QUERIES:
        chosen = select_tool(query, tools, system)
        ok = chosen == expected
        correct += ok
        rows.append((query, expected, chosen))
        print(f"  {'OK ' if ok else 'MIS'}  {chosen:13s}  {query}")
    print(f"  accuracy: {correct}/{len(QUERIES)}")
    return correct, rows


if __name__ == "__main__":
    print("Description quality (five elements):")
    for t in VAGUE_TOOLS + GOOD_TOOLS:
        other = "lookup_order" if t["name"] == "get_customer" else "get_customer"
        q = check_description(t["description"], other)
        print(f"  {t['name']:13s} {'VAGUE' if t in VAGUE_TOOLS else 'GOOD ':5s} {sum(q.values())}/5  {[k for k, v in q.items() if not v]}")

    vague, vague_rows = evaluate(VAGUE_TOOLS, None, "RUN 1 — vague descriptions, no system prompt")
    good, good_rows = evaluate(GOOD_TOOLS, None, "RUN 2 — good descriptions, no system prompt")

    print("\nSystem prompt audit:")
    for p, name in ((CONFLICTING_PROMPT, "CONFLICTING"), (NEUTRAL_PROMPT, "NEUTRAL")):
        hits = audit_system_prompt(p)
        print(f"  {name}: {len(hits)} keyword-sensitive phrase(s)")
        for h in hits:
            print(f"    - \"{h['phrase']}\"\n      -> {h['problem']}")

    conflict, _ = evaluate(GOOD_TOOLS, CONFLICTING_PROMPT, "RUN 3 — good descriptions + CONFLICTING system prompt")
    neutral, _ = evaluate(GOOD_TOOLS, NEUTRAL_PROMPT, "RUN 4 — good descriptions + NEUTRAL system prompt")

    print("\n" + "=" * 60 + "\nSUMMARY\n" + "=" * 60)
    print(f"  vague descriptions                 {vague:2d}/10")
    print(f"  good descriptions                  {good:2d}/10")
    print(f"  good + conflicting system prompt   {conflict:2d}/10")
    print(f"  good + neutral system prompt       {neutral:2d}/10")
    fixed = [q for (q, e, c1), (_, _, c2) in zip(vague_rows, good_rows) if c1 != e and c2 == e]
    print(f"\n  misrouted with vague, fixed by good descriptions ({len(fixed)}):")
    for q in fixed:
        print(f"    - {q}")
