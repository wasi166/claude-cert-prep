"""
Offline checks. No API key needed: pytest -q

1. Distribution: three roles, 4 tools each, disjoint, out of a 15-tool catalogue
2. verify_fact is scoped: single-source only, escalates otherwise, and its description says so
3. load_document refuses non-document and untrusted URLs; fetch_url is assigned to no role
4. tool_choice: forced on the doc agent's first call, any on web-search, auto after the first turn
5. The audit catches cross-role misuse and a skipped mandatory step
"""
import json
import os
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import tool_distribution as lesson  # noqa: E402


# ---- Step 1 -------------------------------------------------------------------------

def test_each_role_has_a_small_scoped_toolset():
    assert len(lesson.CATALOGUE) >= 14                                   # plenty to overload with
    for role, cfg in lesson.ROLES.items():
        assert 4 <= len(cfg["tools"]) <= 5, role
        assert all(t in lesson.CATALOGUE for t in cfg["tools"])


def test_roles_are_disjoint():
    sets = [set(c["tools"]) for c in lesson.ROLES.values()]
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert not (a & b)


def test_dangerous_generic_tools_are_assigned_to_no_role():
    assigned = {t for c in lesson.ROLES.values() for t in c["tools"]}
    for generic in ("fetch_url", "send_email", "run_sql"):
        assert generic in lesson.CATALOGUE and generic not in assigned


# ---- Step 2: scoped cross-role tool -------------------------------------------------------

def test_verify_fact_is_in_synthesis_and_description_states_scope_and_escalation():
    assert "verify_fact" in lesson.ROLES["synthesis"]["tools"]
    desc = lesson.CATALOGUE["verify_fact"][0]
    assert "ONE" in desc and "single-source" in desc
    assert "Do NOT use" in desc and "coordinator" in desc


def test_verify_fact_handles_simple_case_and_escalates_unknown_source():
    ok = json.loads(lesson.verify_fact("597 GW added in 2024", "https://docs.example.org/solar-2024.pdf"))
    assert ok["verified"] is True
    wrong = json.loads(lesson.verify_fact("600 GW added in 2024", "https://docs.example.org/solar-2024.pdf"))
    assert wrong["verified"] is False and "escalate" not in wrong
    esc = json.loads(lesson.verify_fact("anything", "https://unknown.example.com/x.pdf"))
    assert esc["escalate"] is True


# ---- Step 4: least privilege ---------------------------------------------------------------

@pytest.mark.parametrize("url, category", [
    ("http://docs.example.org/solar-2024.pdf", "validation"),       # not https
    ("https://evil.example.com/solar-2024.pdf", "permission"),      # untrusted host
    ("https://docs.example.org/index.html", "validation"),          # not a document
    ("https://docs.example.org/api/v1/users", "validation"),        # an API, not a document
])
def test_load_document_refuses_non_documents(url, category):
    out = json.loads(lesson.load_document(url))
    assert out["isError"] is True and out["errorCategory"] == category and out["isRetryable"] is False


def test_load_document_accepts_trusted_documents_and_fetch_url_accepts_anything():
    assert json.loads(lesson.load_document("https://docs.example.org/solar-2024.pdf"))["loaded"] is True
    assert "body" in json.loads(lesson.fetch_url("https://evil.example.com/anything"))   # the problem we replaced


# ---- Step 3: tool_choice modes -------------------------------------------------------------

class FakeModel:
    """First turn: call whatever tool_choice demands (or the first tool if 'any'/'auto'); then end."""

    def __init__(self):
        self.calls = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if len([c for c in self.calls]) % 2 == 1:                 # odd call -> use a tool
            tc = kw["tool_choice"]
            name = tc["name"] if tc["type"] == "tool" else kw["tools"][0]["name"]
            args = {"url": "https://docs.example.org/solar-2024.pdf"} if "url" in kw["tools"][0]["input_schema"]["properties"] or tc["type"] == "tool" else {"query": "solar"}
            if name == "compile_report":
                args = {"findings": ["x"]}
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t", name=name, input=args)])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="done")])


def test_document_agent_first_call_is_forced_then_auto(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    _, called, choices = lesson.run_role("document-analysis", "analyse")
    assert choices[0] == {"type": "tool", "name": "extract_metadata"}
    assert choices[1] == {"type": "auto"}
    assert called[0] == "extract_metadata"


def test_web_search_first_call_guarantees_a_tool_call(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    _, called, choices = lesson.run_role("web-search", "find facts")
    assert choices[0] == {"type": "any"}
    assert called                                                 # a tool was called on turn 1


def test_synthesis_first_call_is_auto(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    _, _, choices = lesson.run_role("synthesis", "compile")
    assert choices[0] == {"type": "auto"}


def test_only_role_tools_are_sent_to_the_api(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    lesson.run_role("synthesis", "compile")
    sent = {t["name"] for t in fake.calls[0]["tools"]}
    assert sent == set(lesson.ROLES["synthesis"]["tools"])
    assert "web_search" not in sent                               # structurally impossible to misuse


# ---- Step 5: audit ----------------------------------------------------------------------------

def test_audit_passes_clean_log_and_flags_misuse_and_skipped_step():
    clean = {
        "web-search": {"called": ["web_search", "fetch_snippet"], "first_choice": {}},
        "document-analysis": {"called": ["extract_metadata", "load_document"], "first_choice": {}},
        "synthesis": {"called": ["verify_fact", "compile_report"], "first_choice": {}},
    }
    assert lesson.audit(clean) == []

    bad = json.loads(json.dumps(clean))
    bad["synthesis"]["called"] = ["web_search", "compile_report"]          # cross-role misuse
    bad["document-analysis"]["called"] = ["load_document", "extract_metadata"]  # skipped mandatory first step
    problems = lesson.audit(bad)
    assert any("synthesis used tools outside its role" in p for p in problems)
    assert any("did not start with extract_metadata" in p for p in problems)
