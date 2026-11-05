"""
Lesson 2.2 — Structured Error Responses

A tool result is one of three things, and the caller must be able to tell which without reading prose:

    SUCCESS         isError: false, resultCount >= 1
    VALID EMPTY     isError: false, resultCount: 0      <- the query ran fine; there was nothing there
    ERROR           isError: true,  {errorCategory, isRetryable, description}

Four error categories, four recovery actions:

    transient    isRetryable: true    -> retry with backoff (max 3)
    validation   isRetryable: false   -> fix the input, then call again (that's not a retry)
    business     isRetryable: false   -> escalate to a human; never retry
    permission   isRetryable: false   -> obtain elevated credentials, then call again

Run:  python structured_errors.py            # deterministic recovery loop over every scenario
      python structured_errors.py --live     # Claude reads the same metadata and chooses the action
"""

import json
import os
import re
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

MAX_RETRIES = 3
BACKOFF_BASE = 0.5          # seconds; delay = BACKOFF_BASE * 2**attempt
TRANSIENT_FAILS_BEFORE_OK = 2   # the simulated timeout clears on the 3rd try
ID_FORMAT = re.compile(r"^CUST-\d{4}$")

CUSTOMERS = {
    "CUST-0889": {"name": "Ali Raza", "tier": "premium", "refund_limit": 500},
    "CUST-1002": {"name": "Sara Khan", "tier": "standard", "refund_limit": 100},
}
_transient_attempts: dict[str, int] = {}


# ===========================================================================
# Steps 1-4: the tool. Returns MCP CallToolResult shape: {"content": [...], "isError": bool}
# ===========================================================================
def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _error(category: str, retryable: bool, description: str) -> dict:
    """Exactly three fields. Nothing else goes in an error payload."""
    body = {"errorCategory": category, "isRetryable": retryable, "description": description}
    return {"content": [{"type": "text", "text": json.dumps(body)}], "isError": True}


CUSTOMER_LOOKUP_TOOL = {
    "name": "customer_lookup",
    "description": (
        "Look up a customer record by ID (format CUST-####, e.g. CUST-0889). Optional refund_amount checks "
        "the amount against the customer's policy limit. Set elevated=true only after credentials have been "
        "granted. failure_mode is for testing: timeout | access_denied."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "identifier":    {"type": "string"},
            "refund_amount": {"type": "number"},
            "elevated":      {"type": "boolean"},
            "failure_mode":  {"type": "string", "enum": ["timeout", "access_denied"]},
        },
        "required": ["identifier"],
    },
}


def customer_lookup(identifier: str, refund_amount: float | None = None,
                    elevated: bool = False, failure_mode: str | None = None) -> dict:
    # --- permission: the caller lacks rights ---------------------------------
    if failure_mode == "access_denied" and not elevated:
        return _error("permission", False,
                      "Access denied: this record requires the 'support:elevated' scope. "
                      "Request elevated credentials, then call again with elevated=true.")

    # --- transient: the backend timed out (clears after a couple of attempts) --
    if failure_mode == "timeout":
        n = _transient_attempts.get(identifier, 0) + 1
        _transient_attempts[identifier] = n
        if n <= TRANSIENT_FAILS_BEFORE_OK:
            return _error("transient", True,
                          f"Upstream customer database timed out after 5s (attempt {n}). "
                          "Safe to retry with backoff.")

    # --- validation: the input is malformed ---------------------------------
    if not ID_FORMAT.match(identifier):
        return _error("validation", False,
                      f"Invalid identifier '{identifier}': expected format CUST-#### (four digits, e.g. CUST-0889). "
                      "Correct the identifier format and call again; retrying unchanged will fail identically.")

    # --- valid empty: well-formed query, nothing matched ----------------------
    record = CUSTOMERS.get(identifier)
    if record is None:
        return _ok({"resultCount": 0, "results": [],
                    "note": f"No customer with ID {identifier}. The query succeeded; the record does not exist."})

    # --- business: the request violates policy -------------------------------
    if refund_amount is not None and refund_amount > record["refund_limit"]:
        return _error("business", False,
                      f"Refund of ${refund_amount:.2f} exceeds the ${record['refund_limit']} policy limit for a "
                      f"{record['tier']} customer. This cannot be retried; escalate to a human for approval.")

    return _ok({"resultCount": 1, "results": [{"identifier": identifier, **record}]})


def parse(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


# ===========================================================================
# Step 5: recovery loop — branches on metadata, never on prose
# ===========================================================================
def reformat_identifier(raw: str) -> str | None:
    """'889' -> 'CUST-0889', 'cust-889' -> 'CUST-0889'. None if unfixable."""
    digits = re.sub(r"\D", "", raw)
    return f"CUST-{int(digits):04d}" if digits and len(digits) <= 4 else None


def acquire_elevated_credentials() -> bool:
    return True                                   # stand-in for a real credential flow


def escalate(reason: str) -> str:
    return f"ESCALATED to human: {reason}"


def handle_with_recovery(identifier: str, refund_amount=None, failure_mode=None,
                         sleep=time.sleep) -> tuple[str, list[str]]:
    """Returns (outcome, actions_taken). `sleep` is injectable so tests run instantly."""
    actions: list[str] = []
    elevated = False
    retries = 0

    while True:
        result = customer_lookup(identifier, refund_amount, elevated, failure_mode)
        body = parse(result)

        # ---- not an error: success or valid empty. Either way we are DONE. ----
        if not result["isError"]:
            if body["resultCount"] == 0:
                actions.append("report_not_found")
                return f"No customer found for {identifier} (query succeeded, 0 results).", actions
            actions.append("success")
            return f"Found {body['results'][0]['name']}.", actions

        # ---- an error: branch on the category ---------------------------------
        cat, retryable = body["errorCategory"], body["isRetryable"]

        if cat == "transient" and retryable:
            if retries >= MAX_RETRIES:
                actions.append("give_up_after_retries")
                return escalate(f"transient failure persisted after {MAX_RETRIES} retries"), actions
            delay = BACKOFF_BASE * (2 ** retries)
            retries += 1
            actions.append(f"retry_{retries}_after_{delay:g}s")
            sleep(delay)
            continue

        if cat == "validation":
            fixed = reformat_identifier(identifier)
            if fixed is None or fixed == identifier:
                actions.append("escalate_unfixable_input")
                return escalate(body["description"]), actions
            actions.append(f"reformat_input:{identifier}->{fixed}")
            identifier = fixed
            continue

        if cat == "business":
            actions.append("escalate_business")
            return escalate(body["description"]), actions          # never retried

        if cat == "permission":
            if elevated:                                            # already tried with credentials
                actions.append("escalate_permission")
                return escalate("elevated credentials did not grant access"), actions
            actions.append("request_credentials")
            elevated = acquire_elevated_credentials()
            continue

        actions.append("escalate_unknown")
        return escalate(f"unknown error category {cat!r}"), actions


SCENARIOS = [
    ("success",      dict(identifier="CUST-0889")),
    ("valid empty",  dict(identifier="CUST-9999")),
    ("transient",    dict(identifier="CUST-0889", failure_mode="timeout")),
    ("validation",   dict(identifier="889")),
    ("business",     dict(identifier="CUST-1002", refund_amount=750)),
    ("permission",   dict(identifier="CUST-0889", failure_mode="access_denied")),
]


def run_deterministic():
    for label, kwargs in SCENARIOS:
        _transient_attempts.clear()
        outcome, actions = handle_with_recovery(**kwargs, sleep=lambda s: None)
        print(f"{label:12s} -> {' -> '.join(actions)}\n{'':15s}{outcome}\n")


# ===========================================================================
# --live: Claude reads the same metadata and picks the action itself
# ===========================================================================
RECOVERY_POLICY = """You are a support agent. Tool results are structured:
- isError false with resultCount 0 means the lookup SUCCEEDED and found nothing. Report that. Do not retry.
- isError true carries errorCategory and isRetryable. Act by category:
  transient (isRetryable true): retry the same call, at most 3 times.
  validation: fix the identifier to CUST-#### and call again once.
  business: do not retry; tell the user it has been escalated to a human.
  permission: say you are requesting elevated credentials, then call again with elevated=true.
Never retry an error whose isRetryable is false."""


def run_live():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    for label, kwargs in SCENARIOS:
        _transient_attempts.clear()
        hidden_mode = kwargs.get("failure_mode")
        prompt = f"Look up customer {kwargs['identifier']}" + (
            f" and check a refund of ${kwargs['refund_amount']}" if "refund_amount" in kwargs else "")
        print(f"\n[{label}] USER: {prompt}")
        messages = [{"role": "user", "content": prompt}]
        for _ in range(8):
            r = client.messages.create(model="claude-sonnet-4-5", max_tokens=600, system=RECOVERY_POLICY,
                                       tools=[CUSTOMER_LOOKUP_TOOL], messages=messages)
            if r.stop_reason != "tool_use":
                print("  ASSISTANT:", "".join(b.text for b in r.content if b.type == "text").strip())
                break
            messages.append({"role": "assistant", "content": r.content})
            results = []
            for b in r.content:
                if b.type == "tool_use":
                    args = {**b.input}
                    if hidden_mode:                                  # the test harness injects the failure
                        args["failure_mode"] = hidden_mode
                    out = customer_lookup(**args)
                    print(f"  tool({json.dumps(b.input)}) -> isError={out['isError']} {out['content'][0]['text'][:80]}")
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": out["content"][0]["text"], "is_error": out["isError"]})
            messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    run_live() if "--live" in sys.argv else run_deterministic()
