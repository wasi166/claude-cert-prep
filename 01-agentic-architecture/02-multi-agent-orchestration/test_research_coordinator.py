"""
Offline checks for the lesson's core ideas. No API key needed: pytest -q

1. Subagents get everything they need IN THE PROMPT (isolation => explicit context passing)
2. Narrow decomposition is caught by the coverage check and fixed by refinement
3. Refinement stops when coverage is complete, and is bounded by MAX_ROUNDS otherwise
"""
import json
import os
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import research_coordinator as lesson  # noqa: E402


class FakeClaude:
    """Routes each call by what the prompt asks for and records every prompt it was sent."""

    def __init__(self, decomposition, coverage_sequence):
        self.decomposition = decomposition
        self.coverage_sequence = list(coverage_sequence)   # one dict per evaluate_coverage call
        self.calls = []                                     # (system, user) for every call
        self.messages = NS(create=self._create)

    def _create(self, *, system, messages, **_):
        user = messages[0]["content"]
        self.calls.append((system, user))
        if "Decompose the topic" in user or "most well-known examples" in user:
            text = json.dumps(self.decomposition)
        elif "rate coverage" in user:
            text = json.dumps(self.coverage_sequence.pop(0))
        elif "Write a structured research report" in user:
            text = "REPORT: " + user
        else:                                               # a subagent call
            text = f"findings about {user.split('Your assigned subtopic: ')[1].splitlines()[0]}"
        return NS(content=[NS(type="text", text=text)])


def test_subagents_receive_full_context_in_prompt(monkeypatch):
    fake = FakeClaude(["solar", "wind", "geothermal", "tidal", "biomass", "fusion"],
                      [{"solar": "covered", "wind": "covered", "geothermal": "covered",
                        "tidal": "covered", "biomass": "covered", "fusion": "covered"}])
    monkeypatch.setattr(lesson, "client", fake)

    lesson.coordinator("renewable energy technologies")

    spoke_prompts = [u for s, u in fake.calls if "research subagent" in s or "analysis subagent" in s]
    assert len(spoke_prompts) == 12                         # 6 subtopics x 2 spokes
    for p in spoke_prompts:
        assert "Overall research topic: renewable energy technologies" in p
        assert "Research goal:" in p
        assert "Your assigned subtopic:" in p
        assert "Already covered by other subagents" in p

    # The last subtopic's spokes are told what the earlier ones already covered.
    assert "solar, wind, geothermal, tidal, biomass" in spoke_prompts[-1]


def test_narrow_decomposition_is_caught_and_refined(monkeypatch):
    fake = FakeClaude(
        ["solar", "wind"],                                  # the exam's classic bad decomposition
        [
            {"solar": "covered", "wind": "covered", "geothermal": "missing",
             "tidal": "missing", "biomass": "missing", "fusion": "missing"},
            {"solar": "covered", "wind": "covered", "geothermal": "covered",
             "tidal": "covered", "biomass": "covered", "fusion": "covered"},
        ],
    )
    monkeypatch.setattr(lesson, "client", fake)

    report, coverage = lesson.coordinator("renewable energy technologies")

    re_delegated = [u for s, u in fake.calls if ("research subagent" in s or "analysis subagent" in s) and
                    any(f"Your assigned subtopic: {g}" in u for g in ["geothermal", "tidal", "biomass", "fusion"])]
    assert len(re_delegated) == 8                           # 4 gaps x 2 spokes
    assert all(v == "covered" for v in coverage.values())
    assert all(r in report for r in lesson.REQUIRED)        # the final report has every section


def test_refinement_is_bounded_by_max_rounds(monkeypatch):
    never_done = {"solar": "covered", "fusion": "missing"}
    fake = FakeClaude(["solar"], [never_done] * (lesson.MAX_ROUNDS + 2))
    monkeypatch.setattr(lesson, "client", fake)

    _, coverage = lesson.coordinator("renewable energy technologies")

    evaluations = [u for _, u in fake.calls if "rate coverage" in u]
    assert len(evaluations) == lesson.MAX_ROUNDS            # evaluated exactly MAX_ROUNDS times, then stopped
    assert coverage["fusion"] == "missing"                  # and reported the gap honestly
