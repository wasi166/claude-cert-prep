"""
Offline checks. No API key needed: pytest -q

1. The workspace has 10 analysable files with seeded issues; fixes change 3 of them substantively
2. Ground truth is read from the CURRENT files, so it moves when the files move
3. stale_claims() flags advice about code that no longer exists; missed_claims() flags the reverse
4. The summary carries conclusions, not tool output
5. The three strategies are configured correctly: resume carries the id, fork sets fork_session,
   fresh carries the summary and names only the changed files
"""
import json
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import sessions as lesson  # noqa: E402


def setup_function():
    lesson.make_workspace()


# ---- Steps 1 & 3 ------------------------------------------------------------

def test_workspace_has_ten_files_each_with_its_seeded_issue():
    files = [p for p in lesson.WORKSPACE.glob("*.ts") if p.name != "db.ts"]
    assert len(files) == 10
    for name, (pattern, _) in lesson.FILES.items():
        assert lesson.issues_present_now(name) == {pattern}


def test_fixes_remove_the_issue_in_exactly_three_files():
    before = {n: lesson.issues_present_now(n) for n in lesson.FILES}
    lesson.apply_fixes()
    after = {n: lesson.issues_present_now(n) for n in lesson.FILES}
    changed = [n for n in lesson.FILES if before[n] != after[n]]
    assert sorted(changed) == lesson.CHANGED
    assert all(after[n] == set() for n in lesson.CHANGED)         # fully fixed
    assert all(after[n] == before[n] for n in lesson.FILES if n not in lesson.CHANGED)


# ---- Step 6: scoring --------------------------------------------------------------

def stale_review():
    """What a resumed session answering from old tool results would say."""
    return [{"file": n, "issues": [p], "severity": "high", "recommendation": "fix"} for n, (p, _) in lesson.FILES.items()]


def current_review():
    return [{"file": n, "issues": sorted(lesson.issues_present_now(n)), "severity": "high", "recommendation": ""}
            for n in lesson.FILES]


def test_stale_claims_detected_only_after_files_change():
    assert lesson.stale_claims(stale_review()) == []            # before fixes, old advice is correct
    lesson.apply_fixes()
    stale = lesson.stale_claims(stale_review())
    assert len(stale) == 3
    assert all(any(n in s for n in lesson.CHANGED) for s in stale)
    assert lesson.stale_claims(current_review()) == []          # advice from current content is clean


def test_missed_claims_flags_a_review_that_ignores_changed_files():
    lesson.apply_fixes()
    # Pretend a review that says inventory.ts still has its (now fixed) issue, and omits nothing real
    assert lesson.missed_claims(current_review()) == []
    # Now put the issue back in orders.ts on disk and have the review miss it
    old, new = lesson.FIXES["orders.ts"]
    p = lesson.WORKSPACE / "orders.ts"; p.write_text(p.read_text().replace(new, old))
    assert lesson.missed_claims(current_review()) == []         # current_review re-reads disk -> still clean
    review_without_orders = [r for r in current_review() if r["file"] != "orders.ts"]
    assert lesson.missed_claims(review_without_orders) == ["orders.ts: misses SQL_CONCAT"]


# ---- Step 2: the summary ----------------------------------------------------------

def test_summary_is_conclusions_not_tool_output():
    s = lesson.build_summary(stale_review())
    assert set(s["files"]) == set(lesson.FILES)
    for v in s["files"].values():
        assert set(v) == {"issues", "severity", "recommendation"}
    assert "db.query" not in json.dumps(s)                      # no source code / raw Read output inside


# ---- Steps 4-5 + fork: session options ---------------------------------------------

def test_resume_fork_and_fresh_are_configured_differently():
    resume = lesson.base_options(resume="sess-123")
    fork = lesson.base_options(resume="sess-123", fork_session=True)
    fresh = lesson.base_options()
    assert resume.resume == "sess-123" and not resume.fork_session
    assert fork.resume == "sess-123" and fork.fork_session is True
    assert fresh.resume is None and not fresh.fork_session


def test_fresh_prompt_carries_summary_and_names_only_changed_files():
    summary = lesson.build_summary(stale_review())
    prompt = (json.dumps(summary) + f"\n\nSince then, exactly these files changed: {lesson.CHANGED}. "
              "Re-read ONLY those three files")
    for f in lesson.CHANGED:
        assert f in prompt
    assert "Re-read ONLY" in prompt
    assert "users.ts" in prompt                                  # present via the summary...
    assert prompt.count("users.ts") == 1                         # ...but not in the re-read list
