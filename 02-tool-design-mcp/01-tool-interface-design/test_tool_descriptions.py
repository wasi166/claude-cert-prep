"""
Offline checks. No API key needed: pytest -q

1. Vague descriptions fail the five-element check; good ones pass
2. Both versions share identical schemas — the description is the only variable
3. The query set is balanced and includes cross-mention distractors
4. The system prompt audit flags conflicting phrasing and passes neutral phrasing
5. The evaluation harness forces a tool choice and scores selection
"""
import os
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import tool_descriptions as lesson  # noqa: E402


def other(name):
    return "lookup_order" if name == "get_customer" else "get_customer"


# ---- Steps 1 & 3 ------------------------------------------------------------

def test_vague_descriptions_are_one_sentence_with_no_elements():
    for t in lesson.VAGUE_TOOLS:
        assert t["description"].count(".") == 1
        q = lesson.check_description(t["description"], other(t["name"]))
        assert sum(q.values()) == 0, q


def test_good_descriptions_have_all_five_elements():
    for t in lesson.GOOD_TOOLS:
        q = lesson.check_description(t["description"], other(t["name"]))
        assert all(q.values()), {t["name"]: [k for k, v in q.items() if not v]}
        sentences = [s for s in t["description"].split(". ") if s]
        assert 3 <= len(sentences) <= 6


def test_good_descriptions_name_the_other_tool_in_a_boundary():
    for t in lesson.GOOD_TOOLS:
        assert f"use {other(t['name'])} for" in t["description"]


def test_only_the_description_differs_between_versions():
    for v, g in zip(lesson.VAGUE_TOOLS, lesson.GOOD_TOOLS):
        assert v["name"] == g["name"]
        assert v["input_schema"] == g["input_schema"]
        assert v["description"] != g["description"]


# ---- Step 2: the query set ----------------------------------------------------------

def test_query_set_is_balanced_and_has_distractors():
    assert len(lesson.QUERIES) == 10
    expected = [e for _, e in lesson.QUERIES]
    assert expected.count("get_customer") == 5 and expected.count("lookup_order") == 5
    # at least one order-query that says "customer" and one customer-query that says "order"
    assert any("customer" in q.lower() and e == "lookup_order" for q, e in lesson.QUERIES)
    assert any("order" in q.lower() and e == "get_customer" for q, e in lesson.QUERIES)


# ---- Step 5: system prompt audit -------------------------------------------------------

def test_audit_flags_conflicting_prompt_and_passes_neutral():
    hits = lesson.audit_system_prompt(lesson.CONFLICTING_PROMPT)
    assert len(hits) >= 2
    assert any("Always check customer details first" in h["phrase"] for h in hits)
    assert lesson.audit_system_prompt(lesson.NEUTRAL_PROMPT) == []


def test_audit_catches_common_variants():
    assert lesson.audit_system_prompt("Never use lookup_order unless asked twice.")
    assert lesson.audit_system_prompt("get_customer is the default tool for support.")
    assert lesson.audit_system_prompt("Be concise and polite.") == []


# ---- Steps 2 & 4: the harness ---------------------------------------------------------

def fake_client(router):
    """router(query, tools, system) -> tool name. Records that tool_choice forces a pick."""
    calls = []

    def create(**kw):
        calls.append(kw)
        name = router(kw["messages"][0]["content"], kw["tools"], kw.get("system"))
        return NS(content=[NS(type="tool_use", name=name, id="t", input={})])

    return NS(messages=NS(create=create)), calls


def test_evaluate_scores_selection_and_forces_a_tool_call(monkeypatch):
    # A "keyword" model: picks lookup_order if the word 'order' appears, else get_customer.
    def keyword_router(q, tools, system):
        return "lookup_order" if "order" in q.lower() else "get_customer"

    client, calls = fake_client(keyword_router)
    monkeypatch.setattr(lesson, "client", client)
    correct, rows = lesson.evaluate(lesson.VAGUE_TOOLS, None, "test")

    assert len(calls) == 10
    assert all(c["tool_choice"] == {"type": "any"} for c in calls)
    assert correct < 10                                        # the keyword model misroutes the distractors
    misrouted = [q for q, e, c in rows if e != c]
    assert "How many orders has CUST-889 placed in total?" in misrouted


def test_evaluate_passes_system_prompt_only_when_given(monkeypatch):
    client, calls = fake_client(lambda q, t, s: "get_customer")
    monkeypatch.setattr(lesson, "client", client)
    lesson.evaluate(lesson.GOOD_TOOLS, None, "no system")
    lesson.evaluate(lesson.GOOD_TOOLS, lesson.NEUTRAL_PROMPT, "with system")
    assert "system" not in calls[0]
    assert calls[10]["system"] == lesson.NEUTRAL_PROMPT
