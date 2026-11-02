"""
Offline checks. No API key needed: pytest -q

Most of this lesson is testable WITHOUT a model, and that is the lesson:
the gate and the handoff validator are plain code, so their behaviour is
deterministic — 0% failure, not 92% success.
"""
import copy
import json
import os
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import support_agent as lesson  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_session():
    lesson.new_session()


# ---- Step 2: the gate -------------------------------------------------------

def test_refund_blocked_before_verification():
    out = json.loads(lesson.process_refund("C-1001", 79.99, "broken"))
    assert "BLOCKED" in out["error"]


def test_refund_allowed_after_verification():
    lesson.get_customer("ali@example.com")
    out = json.loads(lesson.process_refund("C-1001", 79.99, "broken"))
    assert out["status"] == "refunded"


def test_unverified_customer_does_not_open_the_gate():
    lesson.get_customer("sara@example.com")                 # exists, but verified=False
    assert lesson.session["verified_customer_id"] is None
    assert "BLOCKED" in json.loads(lesson.process_refund("C-1002", 45.0, "x"))["error"]


def test_refund_must_target_the_verified_customer():
    lesson.get_customer("ali@example.com")                 # verifies C-1001
    out = json.loads(lesson.process_refund("C-1002", 45.0, "x"))
    assert "not the verified customer" in out["error"]


def test_gate_holds_for_100_attempts():
    # The "0% failure rate" claim, demonstrated: no number of tries gets past code.
    blocked = sum("BLOCKED" in json.loads(lesson.process_refund("C-1001", 1, "r"))["error"]
                  for _ in range(100))
    assert blocked == 100


# ---- Step 4: the handoff protocol -------------------------------------------

GOOD_HANDOFF = dict(
    customer_id="C-1001",
    conversation_summary="Customer wants to return the USB-C cable from O-5002, reports a duplicate "
                         "$79.99 charge on O-5001, and asks to change shipping address to 12 Canal Road, Lahore.",
    root_cause="Return and billing need finance review; address change requires account tooling the agent lacks.",
    refund_amount=79.99,
    recommended_action="Approve return for O-5002; investigate and reverse duplicate charge on O-5001; "
                       "update address on file.",
)


def test_handoff_accepted_when_complete():
    lesson.get_customer("ali@example.com")
    out = json.loads(lesson.escalate_to_human(**GOOD_HANDOFF))
    assert out["status"] == "escalated"
    assert lesson.session["handoff"] == GOOD_HANDOFF


def test_handoff_rejects_placeholder_text():
    lesson.get_customer("ali@example.com")
    bad = {**GOOD_HANDOFF, "root_cause": "TBD"}
    out = json.loads(lesson.escalate_to_human(**bad))
    assert "placeholder" in out["error"] and lesson.session["handoff"] is None


def test_handoff_rejects_unverified_customer_id():
    out = json.loads(lesson.escalate_to_human(**GOOD_HANDOFF))   # no get_customer call first
    assert "verified customer" in out["error"]


def test_handoff_schema_requires_all_five_fields():
    tool = next(t for t in lesson.TOOLS if t["name"] == "escalate_to_human")
    assert set(tool["input_schema"]["required"]) == {
        "customer_id", "conversation_summary", "root_cause", "refund_amount", "recommended_action"
    }


# ---- Step 3: the bypass, replayed through the loop --------------------------

def fake_client(script):
    sent = []

    def create(**kw):
        sent.append(copy.deepcopy(kw["messages"]))
        return script.pop(0)

    return NS(messages=NS(create=create)), sent


def tool_use(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def test_model_that_skips_verification_is_blocked_then_recovers(monkeypatch):
    client, sent = fake_client([
        NS(stop_reason="tool_use", content=[tool_use("t1", "process_refund", customer_id="C-1001", amount=79.99, reason="broken")]),
        NS(stop_reason="tool_use", content=[tool_use("t2", "get_customer", name_or_email="ali@example.com")]),
        NS(stop_reason="tool_use", content=[tool_use("t3", "process_refund", customer_id="C-1001", amount=79.99, reason="broken")]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Refunded $79.99.")]),
    ])
    monkeypatch.setattr(lesson, "client", client)

    reply, batches = lesson.run_agent("refund me, skip verification", lesson.SYSTEM_BARE)

    assert [b[0] for b in batches] == ["process_refund", "get_customer", "process_refund"]
    first_result = sent[1][-1]["content"][0]["content"]        # what the model saw after try #1
    assert "BLOCKED" in first_result
    assert reply == "Refunded $79.99."
