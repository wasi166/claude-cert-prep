"""
Offline checks. No API key needed: pytest -q

The generator, the scoring, and the artefact detector are deterministic, so they are tested
directly. The two review strategies are exercised with a fake model to confirm each pass sends
what it should (everything at once vs one file at a time) and that scoring sees the difference.
"""
import json
import os
import re
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import make_sample_repo as gen   # noqa: E402
import code_review as lesson     # noqa: E402

SIGNATURE = {   # something on the seeded line must look like the pattern
    "FOREACH_ASYNC": r"\.forEach\(async",
    "LOOSE_EQ": r"== null",
    "UNHANDLED_PROMISE": r"^\s*db\.query\(",
    "SQL_CONCAT": r"\+ id \+",
    "HARDCODED_SECRET": r"sk_live_",
}


# ---- Step 1: the sample repo ------------------------------------------------------

def test_repo_has_at_least_ten_files_and_ground_truth_lines_are_exact():
    truth = gen.build()
    assert len(truth["local"]) >= 10
    for fname, issues in truth["local"].items():
        src = (gen.REPO / fname).read_text().splitlines()
        for i in issues:
            assert re.search(SIGNATURE[i["pattern"]], src[i["line"] - 1]), (fname, i)


def test_same_pattern_is_seeded_in_many_files():
    truth = gen.build()
    files_with_foreach = [f for f, v in truth["local"].items() if any(i["pattern"] == "FOREACH_ASYNC" for i in v)]
    assert len(files_with_foreach) >= 5                       # enough repeats to expose inconsistency
    assert "audit.service.ts" in files_with_foreach            # the exam's "file 11"


def test_cross_file_issues_are_invisible_from_a_single_file():
    for c in gen.CROSS_FILE:
        caller = (gen.REPO / c["file"]).read_text()
        assert "getUser(" in caller                             # the call is there...
        assert "function getUser" not in caller                 # ...but the signature is in another file


# ---- Steps 5-6: scoring and artefacts ---------------------------------------------

def test_scoring_tolerates_small_line_offsets_but_not_wrong_pattern():
    t = lesson.TRUTH["local"]["users.service.ts"]
    hit = [{"file": "users.service.ts", "line": t[0]["line"] + 1, "pattern": t[0]["pattern"]}]
    assert lesson.match_local(hit) == {"users.service.ts": {0}}
    miss = [{"file": "users.service.ts", "line": t[0]["line"], "pattern": "OTHER"}]
    assert lesson.match_local(miss) == {}


def test_artefact_detector_finds_inconsistent_verdicts():
    # Flag forEach-async in users.service.ts only; it exists in 6 other files -> artefact
    t = lesson.TRUTH["local"]["users.service.ts"]
    fe = next(i for i in t if i["pattern"] == "FOREACH_ASYNC")
    found = [{"file": "users.service.ts", "line": fe["line"], "pattern": "FOREACH_ASYNC"}]
    arts = lesson.artefacts(found)
    fa = next(a for a in arts if a["pattern"] == "FOREACH_ASYNC")
    assert fa["flagged"] == ["users.service.ts"]
    assert "audit.service.ts" in fa["missed"]


def test_artefact_detector_is_silent_when_consistent():
    everything = [{"file": f, "line": i["line"], "pattern": i["pattern"]}
                  for f, v in lesson.TRUTH["local"].items() for i in v]
    assert lesson.artefacts(everything) == []


# ---- Steps 2-4: what each pass sends to the model ---------------------------------

class FakeModel:
    """Replays a 'diluted' reviewer: catches everything in the first 4 files, nothing after."""

    def __init__(self):
        self.prompts = []
        self.messages = NS(create=self._create)

    def _create(self, *, messages, **_):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        names = re.findall(r"^### (\S+)$", prompt, flags=re.M)
        if "INTEGRATION pass" in prompt:
            text = json.dumps([{"pattern": "API_MISMATCH", "file": "orders.service.ts", "description": "x"}])
        elif len(names) > 1:                                    # single pass: dilute
            found = [{"file": f, "line": i["line"], "pattern": i["pattern"], "severity": "high", "description": ""}
                     for f in names[:4] for i in lesson.TRUTH["local"][f]]
            text = json.dumps(found)
        else:                                                   # per-file: full attention
            f = names[0]
            found = [{"file": f, "line": i["line"], "pattern": i["pattern"], "severity": "high", "description": ""}
                     for i in lesson.TRUTH["local"][f]]
            text = json.dumps({"issues": found, "exports": [], "external_calls": []})
        return NS(content=[NS(type="text", text=text)])


def test_single_pass_sends_everything_in_one_prompt(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    files = lesson.load_repo()
    lesson.single_pass(files)
    assert len(fake.prompts) == 1
    assert all(f"### {name}" in fake.prompts[0] for name in files)


def test_per_file_pass_sends_one_file_per_prompt(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    files = lesson.load_repo()
    issues, summaries = lesson.per_file_pass(files)
    assert len(fake.prompts) == len(files)
    assert all(len(re.findall(r"^### ", p, flags=re.M)) == 1 for p in fake.prompts)
    assert len(summaries) == len(files)


def test_report_shows_dilution_in_single_pass_and_not_in_multi(monkeypatch):
    fake = FakeModel(); monkeypatch.setattr(lesson, "client", fake)
    files = lesson.load_repo()
    single = lesson.single_pass(files)
    multi_local, summaries = lesson.per_file_pass(files)
    multi_cross = lesson.cross_file_pass(summaries)

    sp = lesson.depth_by_position(single, files)
    assert sum(r[2] for r in sp[-5:]) == 0                      # later files: nothing caught
    assert lesson.artefacts(single)                             # inconsistent verdicts exist
    assert lesson.artefacts(multi_local) == []                  # multi-pass is consistent
    assert len(lesson.match_cross(multi_cross)) == 1
    md = lesson.report(files, single, multi_local, multi_cross)
    assert "attention-dilution artefacts" in md
