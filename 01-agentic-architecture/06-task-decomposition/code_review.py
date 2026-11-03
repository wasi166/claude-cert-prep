"""
Lesson 1.6 — Task Decomposition Strategies: single-pass vs multi-pass code review

    SINGLE PASS      all 15 files in one prompt  ──►  one review
                     (attention spread thin; later files get less of it)

    MULTI PASS       per-file pass: 15 prompts, one file each  ──►  15 structured reviews
                     cross-file pass: one prompt over the 15 summaries  ──►  integration issues
                     (every file gets full attention; cross-cutting issues get their own pass)

Both are scored against ground_truth.json, which the generator wrote with exact line numbers.
The same model runs both. Whatever difference appears is architectural, not a capability gap.

Run:  python make_sample_repo.py      (once)
      python code_review.py           (writes results/comparison.md)
"""

import json
import os
from collections import defaultdict
from pathlib import Path

import anthropic
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-5"
HERE = Path(__file__).parent
REPO = HERE / "sample_repo"
TRUTH = json.loads((HERE / "ground_truth.json").read_text())

PATTERNS = {
    "FOREACH_ASYNC":     "Array.forEach with an async callback — the promises are dropped, callers can't await",
    "LOOSE_EQ":          "== or != null/undefined instead of ===",
    "UNHANDLED_PROMISE": "a promise-returning call whose result is neither awaited nor returned",
    "SQL_CONCAT":        "SQL built by string concatenation with user input",
    "HARDCODED_SECRET":  "a credential or secret literal in source",
    "OTHER":             "anything else worth flagging",
}

ISSUE_FORMAT = (
    "Report issues as a JSON array. Each item: "
    '{"file": "<name>", "line": <int>, "pattern": "<one of ' + "|".join(PATTERNS) + '>", '
    '"severity": "high|medium|low", "description": "<one sentence>"}. '
    "Report every occurrence of a pattern separately, even if it repeats across files. "
    "Respond with ONLY the JSON array."
)
PATTERN_GUIDE = "Patterns to look for:\n" + "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())


def ask(prompt: str, max_tokens: int = 4000) -> str:
    r = client.messages.create(model=MODEL, max_tokens=max_tokens,
                               messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text")


def parse_json(text: str):
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


def load_repo() -> dict[str, str]:
    """Step 1: read every source file. Order matters — it's the order the single pass sees them."""
    return {p.name: p.read_text() for p in sorted(REPO.glob("*.ts"))}


def numbered(src: str) -> str:
    return "\n".join(f"{i:3d}| {ln}" for i, ln in enumerate(src.splitlines(), 1))


# ===========================================================================
# Step 2: single pass — everything in one prompt
# ===========================================================================
def single_pass(files: dict[str, str]) -> list[dict]:
    blob = "\n\n".join(f"### {name}\n{numbered(src)}" for name, src in files.items())
    prompt = (f"Review this TypeScript codebase of {len(files)} files for bugs.\n\n{PATTERN_GUIDE}\n\n"
              f"{ISSUE_FORMAT}\n\n{blob}")
    return parse_json(ask(prompt))


# ===========================================================================
# Step 3: per-file pass — one prompt per file, full attention each time
# ===========================================================================
def per_file_pass(files: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Returns (issues, summaries). Summaries feed the cross-file pass."""
    issues, summaries = [], []
    for name, src in files.items():
        prompt = (f"Review this ONE TypeScript file for bugs.\n\n{PATTERN_GUIDE}\n\n"
                  "Respond with ONLY a JSON object: "
                  '{"issues": [<as described below>], '
                  '"exports": [{"name": "<fn>", "params": "<signature>", "returns": "<shape>"}], '
                  '"external_calls": [{"callee": "<imported fn>", "args": "<how it is called>", '
                  '"fields_read_from_result": ["<field>"]}]}\n'
                  f"For issues: {ISSUE_FORMAT}\n\n### {name}\n{numbered(src)}")
        data = parse_json(ask(prompt, max_tokens=1500)) or {}
        for i in data.get("issues", []):
            i["file"] = name                                   # trust the file name we sent, not the model's
            issues.append(i)
        summaries.append({"file": name, "exports": data.get("exports", []),
                          "external_calls": data.get("external_calls", [])})
        print(f"  per-file: {name:28s} {len(data.get('issues', []))} issues")
    return issues, summaries


# ===========================================================================
# Step 4: cross-file integration pass — summaries only, looking for mismatches
# ===========================================================================
def cross_file_pass(summaries: list[dict]) -> list[dict]:
    prompt = (
        "You are doing the INTEGRATION pass of a code review. You do not see source code — only a "
        "structured summary of each file's exports and the calls it makes into other files.\n"
        "Find cross-file problems:\n"
        "- API_MISMATCH: a caller passes arguments that don't match the callee's exported signature\n"
        "- FIELD_MISMATCH: a caller reads a field from a result that the callee does not return\n"
        "- PATTERN_INCONSISTENCY: the same concern handled differently in different files\n"
        "Respond with ONLY a JSON array of "
        '{"pattern": "<one of the three>", "file": "<the CALLER file>", "description": "<one sentence>"}.\n\n'
        + json.dumps(summaries, indent=1)
    )
    return parse_json(ask(prompt, max_tokens=2000))


# ===========================================================================
# Step 5–6: scoring against ground truth, and artefact detection
# ===========================================================================
def match_local(found: list[dict]) -> dict[str, set[int]]:
    """For each file, which ground-truth issue indices did the review catch? (same pattern, line within ±2)"""
    caught = defaultdict(set)
    for f in found:
        truths = TRUTH["local"].get(f.get("file", ""), [])
        for idx, t in enumerate(truths):
            try:
                if f.get("pattern") == t["pattern"] and abs(int(f.get("line", -99)) - t["line"]) <= 2:
                    caught[f["file"]].add(idx)
            except (TypeError, ValueError):
                pass
    return caught


def match_cross(found: list[dict]) -> set[int]:
    caught = set()
    for f in found:
        for idx, t in enumerate(TRUTH["cross_file"]):
            if f.get("pattern") == t["pattern"] and f.get("file") == t["file"]:
                caught.add(idx)
    return caught


def artefacts(found: list[dict]) -> list[dict]:
    """Step 6: a pattern caught in some files but missed in others where the identical code exists."""
    caught = match_local(found)
    by_pattern = defaultdict(lambda: {"flagged": [], "missed": []})
    for fname, truths in TRUTH["local"].items():
        for idx, t in enumerate(truths):
            key = "flagged" if idx in caught.get(fname, set()) else "missed"
            by_pattern[t["pattern"]][key].append(fname)
    return [{"pattern": p, **v} for p, v in by_pattern.items() if v["flagged"] and v["missed"]]


def depth_by_position(found: list[dict], files: dict[str, str]) -> list[tuple[int, str, int, int]]:
    """(position, file, caught, total) in the order the single pass saw the files."""
    caught = match_local(found)
    return [(i, name, len(caught.get(name, set())), len(TRUTH["local"][name]))
            for i, name in enumerate(files, 1)]


def report(files, single, multi_local, multi_cross) -> str:
    sp, mp = depth_by_position(single, files), depth_by_position(multi_local, files)
    total = sum(len(v) for v in TRUTH["local"].values())
    s_caught, m_caught = sum(r[2] for r in sp), sum(r[2] for r in mp)
    s_cross, m_cross = len(match_cross(single)), len(match_cross(multi_cross))
    s_art, m_art = artefacts(single), artefacts(multi_local)

    lines = ["# Single-pass vs multi-pass review\n",
             "## Totals\n",
             "| | single pass | multi pass |", "|---|---|---|",
             f"| local issues caught / {total} | {s_caught} | {m_caught} |",
             f"| cross-file issues caught / {len(TRUTH['cross_file'])} | {s_cross} | {m_cross} (integration pass) |",
             f"| attention-dilution artefacts | {len(s_art)} | {len(m_art)} |",
             "\n## Depth by file position (caught / seeded)\n",
             "| # | file | single | multi |", "|---|---|---|---|"]
    for (i, name, sc, st), (_, _, mc, _) in zip(sp, mp):
        lines.append(f"| {i} | {name} | {sc}/{st} | {mc}/{st} |")
    first, last = sp[:5], sp[-5:]
    lines += ["", f"single pass — first 5 files: {sum(r[2] for r in first)}/{sum(r[3] for r in first)} ; "
                  f"last 5 files: {sum(r[2] for r in last)}/{sum(r[3] for r in last)}"]
    first, last = mp[:5], mp[-5:]
    lines += [f"multi pass  — first 5 files: {sum(r[2] for r in first)}/{sum(r[3] for r in first)} ; "
              f"last 5 files: {sum(r[2] for r in last)}/{sum(r[3] for r in last)}"]
    lines += ["\n## Artefacts: same pattern, different verdict\n"]
    for label, arts in (("single pass", s_art), ("multi pass", m_art)):
        lines.append(f"**{label}**: " + ("none" if not arts else ""))
        for a in arts:
            lines.append(f"- `{a['pattern']}` flagged in {a['flagged']} but missed in {a['missed']}")
    lines += ["\n## Cross-file issues\n"]
    for idx, t in enumerate(TRUTH["cross_file"]):
        lines.append(f"- {t['pattern']} in {t['file']}: {t['detail']} — "
                     f"single: {'caught' if idx in match_cross(single) else 'missed'}, "
                     f"multi: {'caught' if idx in match_cross(multi_cross) else 'missed'}")
    return "\n".join(lines)


if __name__ == "__main__":
    files = load_repo()
    print(f"loaded {len(files)} files\n")

    print("SINGLE PASS (one prompt, all files)")
    single = single_pass(files)
    print(f"  {len(single)} issues reported\n")

    print("MULTI PASS — per-file")
    multi_local, summaries = per_file_pass(files)
    print("MULTI PASS — cross-file integration")
    multi_cross = cross_file_pass(summaries)
    print(f"  {len(multi_cross)} cross-file issues reported\n")

    md = report(files, single, multi_local, multi_cross)
    out = HERE / "results"; out.mkdir(exist_ok=True)
    (out / "comparison.md").write_text(md)
    (out / "raw.json").write_text(json.dumps({"single": single, "multi_local": multi_local, "multi_cross": multi_cross}, indent=1))
    print(md)
    print(f"\nwritten to {out / 'comparison.md'}")
