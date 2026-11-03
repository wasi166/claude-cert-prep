"""
Offline checks. No API key, no SDK subprocess: pytest -q

Hooks are plain async functions, so every one of them can be called directly with a
fake hook input. That is the lesson: enforcement you can unit-test is enforcement
that holds 100% of the time.
"""
import asyncio
import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import hooks_agent as lesson  # noqa: E402


def call(hook, **input_data):
    return asyncio.run(hook(input_data, "toolu_test", {"signal": None}))


@pytest.fixture(autouse=True)
def fresh_state():
    lesson.session["aml_passed"] = False
    lesson.EXECUTED.clear()


# ---- Step 2: normalisation ----------------------------------------------------

def test_dates_become_iso_8601():
    assert lesson.to_iso(1730419200) == "2024-11-01T00:00:00Z"            # epoch
    assert lesson.to_iso("04/11/2024") == "2024-11-04T00:00:00Z"          # DD/MM/YYYY -> 4 November, not 11 April
    assert lesson.to_iso("2024-11-03T09:15:00Z") == "2024-11-03T09:15:00Z"  # already ISO, untouched


def test_statuses_become_english():
    assert lesson.to_status(1) == "pending"
    assert lesson.to_status("P") == "pending"                              # P is pending, not processed
    assert lesson.to_status("C") == "cancelled"
    assert lesson.to_status("delivered") == "delivered"


def test_post_hook_rewrites_output_in_place():
    raw = lesson._result([{"order_id": "P-1", "created_at": "04/11/2024", "status": "P"}])
    out = call(lesson.normalise_hook, tool_name="mcp__fin__orders_partner",
               tool_input={}, tool_response=raw, hook_event_name="PostToolUse")

    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    rewritten = json.loads(spec["updatedToolOutput"]["content"][0]["text"])
    assert rewritten == [{"order_id": "P-1", "created_at": "2024-11-04T00:00:00Z", "status": "pending"}]
    assert json.loads(raw["content"][0]["text"])[0]["status"] == "P"       # original untouched


# ---- Step 4: threshold policy ---------------------------------------------------

def test_refund_over_limit_is_denied_with_reason():
    out = call(lesson.refund_limit_hook, tool_name="mcp__fin__process_refund",
               tool_input={"customer_id": "C-1", "amount": 750, "reason": "x"}, hook_event_name="PreToolUse")
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "human" in spec["permissionDecisionReason"].lower()


def test_refund_at_or_under_limit_proceeds():
    for amount in (500, 120, 0.01):
        out = call(lesson.refund_limit_hook, tool_name="mcp__fin__process_refund",
                   tool_input={"customer_id": "C-1", "amount": amount, "reason": "x"}, hook_event_name="PreToolUse")
        assert out == {}


# ---- Step 5: prerequisite policy ------------------------------------------------

def test_transfer_denied_until_aml_passes():
    out = call(lesson.aml_gate_hook, tool_name="mcp__fin__transfer_funds",
               tool_input={"customer_id": "C-1", "amount": 300}, hook_event_name="PreToolUse")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "aml_check" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_aml_pass_is_recorded_by_post_hook_and_opens_the_gate():
    call(lesson.aml_record_hook, tool_name="mcp__fin__aml_check", tool_input={"customer_id": "C-1"},
         tool_response=lesson._result({"customer_id": "C-1", "result": "pass"}), hook_event_name="PostToolUse")
    assert lesson.session["aml_passed"] is True

    out = call(lesson.aml_gate_hook, tool_name="mcp__fin__transfer_funds",
               tool_input={"customer_id": "C-1", "amount": 300}, hook_event_name="PreToolUse")
    assert out == {}


def test_aml_fail_does_not_open_the_gate():
    call(lesson.aml_record_hook, tool_name="mcp__fin__aml_check", tool_input={"customer_id": "C-1"},
         tool_response=lesson._result({"customer_id": "C-1", "result": "fail"}), hook_event_name="PostToolUse")
    assert lesson.session["aml_passed"] is False


# ---- Wiring: the right hook on the right event ----------------------------------

def test_hooks_are_registered_on_the_correct_events():
    hooks = lesson.OPTIONS.hooks
    pre = {m.matcher: [h.__name__ for h in m.hooks] for m in hooks["PreToolUse"]}
    post = {m.matcher: [h.__name__ for h in m.hooks] for m in hooks["PostToolUse"]}

    assert pre["mcp__fin__process_refund"] == ["refund_limit_hook"]      # blocking -> Pre
    assert pre["mcp__fin__transfer_funds"] == ["aml_gate_hook"]          # blocking -> Pre
    assert post["mcp__fin__aml_check"] == ["aml_record_hook"]            # recording -> Post
    assert any("orders_legacy" in m and "normalise_hook" in v for m, v in post.items())  # rewriting -> Post
    assert not any("normalise_hook" in v for v in pre.values())          # never normalise before execution
