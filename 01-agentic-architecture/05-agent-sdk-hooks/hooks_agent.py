"""
Lesson 1.5 — Agent SDK Hooks: normalisation (PostToolUse) and policy enforcement (PreToolUse)

    model wants a tool ──► PreToolUse hook ──► tool handler runs ──► PostToolUse hook ──► model sees result
                           │ can DENY:                               │ can REWRITE:
                           │ handler never runs                      │ model sees the rewritten output
                           ▼                                         ▼
                   policy enforcement                         data normalisation
                   (refund > $500, AML gate)                  (dates -> ISO 8601, status -> English)

Three things to notice:
  * A PreToolUse deny happens BEFORE execution. The handler is never called. That is why it is
    the only correct hook for blocking — a PostToolUse "block" would fire after the money moved.
  * A PostToolUse rewrite happens AFTER execution but BEFORE the model reads the result. That is
    why it is the right hook for normalisation.
  * Hooks are code, so they are 100%. The exam's rule: hooks for requirements, prompts for preferences.

Run:  python hooks_agent.py           (needs ANTHROPIC_API_KEY in the repo-root .env; pip install claude-agent-sdk)
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, TextBlock,
    ToolUseBlock, create_sdk_mcp_server, query, tool,
)
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# Session state: written by hooks, read by hooks. The model has no tool that touches it.
session = {"aml_passed": False}
EXECUTED: list[str] = []          # every tool handler appends its name here — proof of what actually ran


def _result(payload) -> dict:
    """MCP tool results are a list of content blocks; we return JSON text."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# ===========================================================================
# Step 1: Three data tools, three incompatible formats.
# ===========================================================================
@tool("orders_legacy", "Orders from the legacy system. Dates are Unix epoch seconds; status is a numeric code.", {"customer_id": str})
async def orders_legacy(args):
    EXECUTED.append("orders_legacy")
    return _result([
        {"order_id": "L-1", "created_at": 1730419200, "status": 1},   # 1 = pending
        {"order_id": "L-2", "created_at": 1730505600, "status": 3},   # 3 = shipped
    ])


@tool("orders_modern", "Orders from the current platform. Dates are ISO 8601; status is an English word.", {"customer_id": str})
async def orders_modern(args):
    EXECUTED.append("orders_modern")
    return _result([
        {"order_id": "M-1", "created_at": "2024-11-03T09:15:00Z", "status": "delivered"},
    ])


@tool("orders_partner", "Orders from a partner feed. Dates are DD/MM/YYYY; status is a single letter.", {"customer_id": str})
async def orders_partner(args):
    EXECUTED.append("orders_partner")
    return _result([
        {"order_id": "P-1", "created_at": "04/11/2024", "status": "P"},   # P = pending (NOT processed)
        {"order_id": "P-2", "created_at": "05/11/2024", "status": "C"},   # C = cancelled
    ])


# ===========================================================================
# Financial tools — the ones the PreToolUse hooks guard.
# ===========================================================================
@tool("process_refund", "Refund an amount to a customer.", {"customer_id": str, "amount": float, "reason": str})
async def process_refund(args):
    EXECUTED.append("process_refund")                      # if a hook denied, this line never runs
    return _result({"status": "refunded", **args})


@tool("aml_check", "Run an anti-money-laundering check on a customer. Returns pass or fail.", {"customer_id": str})
async def aml_check(args):
    EXECUTED.append("aml_check")
    return _result({"customer_id": args["customer_id"], "result": "pass", "checked_at": "2024-11-05T10:00:00Z"})


@tool("transfer_funds", "Transfer funds to a customer's bank account.", {"customer_id": str, "amount": float})
async def transfer_funds(args):
    EXECUTED.append("transfer_funds")
    return _result({"status": "transferred", **args})


FIN_SERVER = create_sdk_mcp_server(
    "fin", tools=[orders_legacy, orders_modern, orders_partner, process_refund, aml_check, transfer_funds],
)
# In-process MCP tools are exposed to the model as  mcp__<server>__<tool>
T = lambda name: f"mcp__fin__{name}"  # noqa: E731


# ===========================================================================
# Step 2: Normalisation — pure functions, then the PostToolUse hook that applies them.
# ===========================================================================
STATUS_CODES = {
    1: "pending", 2: "processing", 3: "shipped", 4: "delivered", 5: "cancelled",
    "P": "pending", "R": "processing", "S": "shipped", "D": "delivered", "C": "cancelled",
}
DDMMYYYY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def to_iso(value):
    if isinstance(value, (int, float)):                    # epoch seconds
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str) and (m := DDMMYYYY.match(value)):
        d, mth, y = m.groups()
        return f"{y}-{mth}-{d}T00:00:00Z"
    return value                                          # already ISO (or not a date)


def to_status(value):
    return STATUS_CODES.get(value, value if isinstance(value, str) else str(value)).lower()


def normalise_record(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if k.endswith(("_at", "_date")) or k in ("date", "timestamp"):
            out[k] = to_iso(v)
        elif k == "status":
            out[k] = to_status(v)
        else:
            out[k] = v
    return out


def normalise_tool_response(tool_response):
    """Rewrite every JSON text block in an MCP result. Returns a new object of the same shape."""
    if not isinstance(tool_response, dict) or "content" not in tool_response:
        return tool_response
    new_blocks = []
    for block in tool_response["content"]:
        if block.get("type") == "text":
            try:
                data = json.loads(block["text"])
                data = [normalise_record(r) for r in data] if isinstance(data, list) else normalise_record(data)
                block = {**block, "text": json.dumps(data)}
            except (json.JSONDecodeError, AttributeError):
                pass
        new_blocks.append(block)
    return {**tool_response, "content": new_blocks}


async def normalise_hook(input_data, tool_use_id, context):
    """PostToolUse: the handler has run; rewrite its output before the model sees it."""
    rewritten = normalise_tool_response(input_data["tool_response"])
    print(f"  [PostToolUse] normalised {input_data['tool_name']}")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": rewritten}}


# ===========================================================================
# Steps 4-5: Policy enforcement — PreToolUse hooks that DENY before execution.
# ===========================================================================
REFUND_LIMIT = 500.0


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


async def refund_limit_hook(input_data, tool_use_id, context):
    """PreToolUse on process_refund: threshold policy."""
    amount = float(input_data["tool_input"].get("amount", 0))
    if amount > REFUND_LIMIT:
        print(f"  [PreToolUse] DENY process_refund ${amount:.2f} > ${REFUND_LIMIT:.0f}")
        return deny(f"Refunds over ${REFUND_LIMIT:.0f} require human approval. Do not retry; tell the "
                    f"customer this has been escalated to a human agent for review.")
    return {}                                              # empty = no opinion, proceed


async def aml_gate_hook(input_data, tool_use_id, context):
    """PreToolUse on transfer_funds: prerequisite policy."""
    if not session["aml_passed"]:
        print("  [PreToolUse] DENY transfer_funds: no passing AML check in session")
        return deny("transfer_funds is blocked until aml_check has returned 'pass' in this session. "
                    "Run aml_check for this customer first, then retry the transfer.")
    return {}


async def aml_record_hook(input_data, tool_use_id, context):
    """PostToolUse on aml_check: record the outcome in session state."""
    text = input_data["tool_response"]["content"][0]["text"]
    if json.loads(text).get("result") == "pass":
        session["aml_passed"] = True
        print("  [PostToolUse] aml_check passed -> session['aml_passed'] = True")
    return {}


# ===========================================================================
# Wiring: which hook fires for which tool.
# ===========================================================================
OPTIONS = ClaudeAgentOptions(
    system_prompt=(
        "You are a finance-operations assistant. Use the tools to answer. When a tool is denied, "
        "do exactly what the denial reason says. Report dates and statuses exactly as the tools return them."
    ),
    mcp_servers={"fin": FIN_SERVER},
    allowed_tools=[T(n) for n in ("orders_legacy", "orders_modern", "orders_partner",
                                  "process_refund", "aml_check", "transfer_funds")],
    permission_mode="bypassPermissions",
    hooks={
        "PreToolUse": [
            HookMatcher(matcher=T("process_refund"), hooks=[refund_limit_hook]),
            HookMatcher(matcher=T("transfer_funds"), hooks=[aml_gate_hook]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="|".join(T(n) for n in ("orders_legacy", "orders_modern", "orders_partner")),
                        hooks=[normalise_hook]),
            HookMatcher(matcher=T("aml_check"), hooks=[aml_record_hook]),
        ],
    },
    max_turns=12,
)


async def run(prompt: str) -> str:
    print(f"\nUSER: {prompt}")
    async for m in query(prompt=prompt, options=OPTIONS):
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, ToolUseBlock):
                    print(f"  model -> {b.name}({json.dumps(b.input)})")
        elif isinstance(m, ResultMessage):
            print(f"ASSISTANT: {m.result}\n")
            return m.result or ""
    return ""


# ===========================================================================
# Steps 3 and 6: scenarios
# ===========================================================================
async def main():
    print("=" * 72 + "\nSCENARIO 1 — normalisation across three formats\n" + "=" * 72)
    reply = await run("List every order for customer C-1 from all three order systems, with date and status.")
    print("CHECK: all three sources called:", {"orders_legacy", "orders_modern", "orders_partner"} <= set(EXECUTED))
    print("CHECK: reply uses English statuses, not codes:", "pending" in reply.lower() and " P " not in reply)

    print("=" * 72 + "\nSCENARIO 2 — refund threshold (PreToolUse deny)\n" + "=" * 72)
    EXECUTED.clear()
    await run("Refund $750 to customer C-1 for order L-2, reason: damaged.")
    print("CHECK: process_refund handler never ran:", "process_refund" not in EXECUTED)
    EXECUTED.clear()
    await run("Refund $120 to customer C-1 for order L-2, reason: damaged.")
    print("CHECK: process_refund handler ran for $120:", "process_refund" in EXECUTED)

    print("=" * 72 + "\nSCENARIO 3 — AML prerequisite (PreToolUse deny until PostToolUse records a pass)\n" + "=" * 72)
    EXECUTED.clear(); session["aml_passed"] = False
    await run("Transfer $300 to customer C-1's bank account.")
    print("CHECK: transfer blocked first, then aml_check, then transfer ran:",
          EXECUTED.index("aml_check") < EXECUTED.index("transfer_funds") if "transfer_funds" in EXECUTED else False)
    print("EXECUTED:", EXECUTED)


if __name__ == "__main__":
    asyncio.run(main())
