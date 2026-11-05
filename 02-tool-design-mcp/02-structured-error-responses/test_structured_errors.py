"""
Offline checks. No API key needed: pytest -q

The tool and the recovery loop are pure functions, so every category and every branch is
tested directly — including the two canonical anti-patterns: retrying a valid empty result,
and retrying an isRetryable:false error.
"""
import json

import pytest

import structured_errors as lesson

CATEGORIES = {"transient", "validation", "business", "permission"}


@pytest.fixture(autouse=True)
def reset():
    lesson._transient_attempts.clear()


def body(result):
    return json.loads(result["content"][0]["text"])


# ---- Steps 2-3: four categories, exactly three fields each ---------------------------

@pytest.mark.parametrize("kwargs, category, retryable", [
    (dict(identifier="CUST-0889", failure_mode="timeout"), "transient", True),
    (dict(identifier="889"), "validation", False),
    (dict(identifier="CUST-1002", refund_amount=750), "business", False),
    (dict(identifier="CUST-0889", failure_mode="access_denied"), "permission", False),
])
def test_each_category_has_correct_shape(kwargs, category, retryable):
    r = lesson.customer_lookup(**kwargs)
    assert r["isError"] is True
    b = body(r)
    assert set(b) == {"errorCategory", "isRetryable", "description"}
    assert b["errorCategory"] == category and b["errorCategory"] in CATEGORIES
    assert b["isRetryable"] is retryable
    assert len(b["description"]) > 30                       # says what happened AND what to do


def test_only_transient_is_retryable():
    results = [lesson.customer_lookup(**kw) for kw, *_ in [
        (dict(identifier="CUST-0889", failure_mode="timeout"),),
        (dict(identifier="889"),),
        (dict(identifier="CUST-1002", refund_amount=750),),
        (dict(identifier="CUST-0889", failure_mode="access_denied"),),
    ]]
    retryable = {body(r)["errorCategory"] for r in results if body(r)["isRetryable"]}
    assert retryable == {"transient"}


# ---- Step 4: valid empty is NOT an error -----------------------------------------------

def test_valid_empty_is_structurally_different_from_an_error():
    empty = lesson.customer_lookup("CUST-9999")
    err = lesson.customer_lookup("CUST-0889", failure_mode="timeout")
    assert empty["isError"] is False and body(empty)["resultCount"] == 0
    assert err["isError"] is True and "resultCount" not in body(err)
    assert "errorCategory" not in body(empty)


# ---- Step 5: the recovery loop branches on metadata ------------------------------------

def run(**kw):
    return lesson.handle_with_recovery(**kw, sleep=lambda s: None)


def test_valid_empty_is_reported_not_retried():
    outcome, actions = run(identifier="CUST-9999")
    assert actions == ["report_not_found"]                   # exactly one call, no retry
    assert "0 results" in outcome


def test_transient_retries_with_exponential_backoff_then_succeeds():
    delays = []
    outcome, actions = lesson.handle_with_recovery("CUST-0889", failure_mode="timeout", sleep=delays.append)
    assert actions == ["retry_1_after_0.5s", "retry_2_after_1s", "success"]
    assert delays == [0.5, 1.0]                              # each delay longer than the last


def test_transient_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(lesson, "TRANSIENT_FAILS_BEFORE_OK", 99)   # never clears
    outcome, actions = run(identifier="CUST-0889", failure_mode="timeout")
    assert actions[-1] == "give_up_after_retries"
    assert sum(a.startswith("retry_") for a in actions) == lesson.MAX_RETRIES
    assert outcome.startswith("ESCALATED")


def test_validation_reformats_input_once_and_does_not_retry_unchanged():
    outcome, actions = run(identifier="889")
    assert actions == ["reformat_input:889->CUST-0889", "success"]


def test_validation_unfixable_input_escalates():
    outcome, actions = run(identifier="not-an-id-at-all-12345")
    assert actions == ["escalate_unfixable_input"]


def test_business_is_escalated_and_never_retried():
    calls = []
    original = lesson.customer_lookup

    def counting(*a, **k):
        calls.append(1)
        return original(*a, **k)

    lesson.customer_lookup = counting
    try:
        outcome, actions = run(identifier="CUST-1002", refund_amount=750)
    finally:
        lesson.customer_lookup = original
    assert actions == ["escalate_business"]
    assert len(calls) == 1                                   # one call; the policy answer won't change
    assert "ESCALATED" in outcome


def test_permission_requests_credentials_then_succeeds():
    outcome, actions = run(identifier="CUST-0889", failure_mode="access_denied")
    assert actions == ["request_credentials", "success"]


def test_live_tool_result_marks_is_error_from_structure():
    # The Messages-API tool_result flag must come from isError, not from reading the text.
    err = lesson.customer_lookup("889")
    empty = lesson.customer_lookup("CUST-9999")
    assert err["isError"] is True and empty["isError"] is False
