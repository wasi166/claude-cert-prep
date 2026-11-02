"""
Lesson 1.2 — Multi-Agent Orchestration: a hub-and-spoke research coordinator

                    ┌──────────────────┐
     topic ───────► │   COORDINATOR    │ ───────► research report
                    │  (the hub)       │
                    │  - decompose     │
                    │  - delegate      │
                    │  - evaluate      │
                    │  - refine        │
                    └───┬──────────┬───┘
                        │          │           spokes never talk to each other,
                 ┌──────▼───┐  ┌───▼───────┐   never see the coordinator's memory,
                 │ web      │  │ document  │   and only know what is written into
                 │ search   │  │ analysis  │   the prompt the coordinator sends.
                 │ subagent │  │ subagent  │
                 └──────────┘  └───────────┘

Run:  python research_coordinator.py              # good decomposition
      python research_coordinator.py --narrow     # deliberately bad decomposition, watch refinement
      (reads ANTHROPIC_API_KEY from the repo-root .env)
"""

import json
import os
import sys

import anthropic
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-5"
MIN_SUBTOPICS = 5     # Step 2: decomposition must cover the breadth of the field
MAX_ROUNDS = 3        # Step 5: refinement rounds. A bound, not the normal exit.


def ask(system: str, user: str, max_tokens: int = 800) -> str:
    """One stateless call. Every call here starts from a blank slate — that is the point."""
    response = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def parse_json(text: str):
    """Claude sometimes wraps JSON in ``` fences. Strip them and parse."""
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(text.strip())


# ===========================================================================
# THE SPOKES — two subagents.
# Each is a fresh call with its own system prompt. No shared messages list,
# no memory of the coordinator or of each other. Whatever they need, the
# coordinator must write into `user`. If a spoke does a bad job, the first
# question is: what did the coordinator forget to tell it?
# ===========================================================================

def web_search_subagent(topic: str, goal: str, subtopic: str, known_so_far: list[str]) -> str:
    snippets = search_stub(subtopic)                      # the spoke's one external resource
    return ask(
        system="You are a web research subagent. You have NO memory of any other task. "
               "Work only from the information in this message. Write 3-5 factual sentences.",
        user=f"""Overall research topic: {topic}
Research goal: {goal}
Your assigned subtopic: {subtopic}
Already covered by other subagents (do not repeat): {', '.join(known_so_far) or 'nothing yet'}

Search results for "{subtopic}":
{snippets}

Summarise what these results tell us about the subtopic.""",
    )


def document_analysis_subagent(topic: str, goal: str, subtopic: str, known_so_far: list[str]) -> str:
    docs = document_corpus(subtopic)                      # coordinator hands over the documents
    return ask(
        system="You are a document analysis subagent. You have NO memory of any other task and "
               "NO access to anything except the documents in this message. If no documents are "
               "provided, say exactly: 'No documents provided for this subtopic.'",
        user=f"""Overall research topic: {topic}
Research goal: {goal}
Your assigned subtopic: {subtopic}
Already covered by other subagents (do not repeat): {', '.join(known_so_far) or 'nothing yet'}

Documents:
{docs or '(none)'}

Extract the key technical and economic facts relevant to the subtopic.""",
    )


# ===========================================================================
# THE HUB — the coordinator owns decomposition, delegation, evaluation, refinement.
# ===========================================================================

COORDINATOR_SYSTEM = (
    "You are the coordinator of a hub-and-spoke research team. You own task decomposition, "
    "delegation to subagents, evaluation of coverage, and the final report. Subagents have no "
    "memory and cannot see each other; you must pass them everything they need. You are "
    "responsible for breadth: a report that misses whole categories of the field is your failure."
)


def decompose(topic: str, narrow: bool = False) -> list[str]:
    """Step 2. Break the topic into subtopics that span the WHOLE field."""
    if narrow:
        # The classic failure: a decomposition that only names the two most famous examples.
        instruction = f"List the 2 most well-known examples of '{topic}'."
    else:
        instruction = (
            f"Decompose the topic '{topic}' into at least {MIN_SUBTOPICS} distinct subtopics that "
            "together cover the FULL breadth of the field — include established, niche, and "
            "emerging/experimental categories, not just the most popular ones."
        )
    raw = ask(COORDINATOR_SYSTEM, instruction + ' Respond with ONLY a JSON array of short strings.', 300)
    return parse_json(raw)


def delegate(topic: str, goal: str, subtopic: str, known_so_far: list[str]) -> dict:
    """Step 3. Spawn both spokes for one subtopic, passing full context to each."""
    return {
        "web": web_search_subagent(topic, goal, subtopic, known_so_far),
        "docs": document_analysis_subagent(topic, goal, subtopic, known_so_far),
    }


def evaluate_coverage(topic: str, findings: dict[str, dict]) -> dict[str, str]:
    """Step 4. Does what we have cover the whole field? Returns {subtopic: covered|partial|missing}."""
    summary = "\n\n".join(
        f"## {s}\nWEB: {f['web']}\nDOCS: {f['docs']}" for s, f in findings.items()
    )
    raw = ask(
        COORDINATOR_SYSTEM,
        f"""Research topic: {topic}

Findings gathered so far:
{summary}

Think about every major category of '{topic}' that a complete report must include — not just the
ones above. For each such category, rate coverage as "covered" (substantive facts present),
"partial" (mentioned but thin), or "missing" (absent). Respond with ONLY a JSON object mapping
category name -> rating.""",
        max_tokens=400,
    )
    return parse_json(raw)


def write_report(topic: str, findings: dict[str, dict]) -> str:
    body = "\n\n".join(f"## {s}\n{f['web']}\n\n{f['docs']}" for s, f in findings.items())
    return ask(
        COORDINATOR_SYSTEM,
        f"Write a structured research report on '{topic}' with one section per subtopic below. "
        f"Keep every section; do not drop any.\n\n{body}",
        max_tokens=2000,
    )


def coordinator(topic: str, narrow: bool = False) -> tuple[str, dict[str, str]]:
    """Step 1. The hub. Takes a topic, returns (report, final coverage assessment)."""
    goal = f"Produce a complete, balanced research report on {topic} covering every major category."

    # --- decompose -------------------------------------------------------
    subtopics = decompose(topic, narrow)
    print(f"[coordinator] decomposed into {len(subtopics)} subtopics: {subtopics}")

    # --- delegate --------------------------------------------------------
    findings: dict[str, dict] = {}
    for st in subtopics:
        print(f"[coordinator] delegating '{st}' to both subagents")
        findings[st] = delegate(topic, goal, st, known_so_far=list(findings))

    # --- evaluate, then refine until coverage is sufficient (Step 5) -----
    coverage: dict[str, str] = {}
    for round_no in range(1, MAX_ROUNDS + 1):
        coverage = evaluate_coverage(topic, findings)
        gaps = [s for s, status in coverage.items() if status != "covered"]
        pct = 100 * (len(coverage) - len(gaps)) // max(len(coverage), 1)
        print(f"[coordinator] round {round_no}: coverage {pct}%  gaps={gaps}")

        if not gaps:
            break                                           # normal exit: coverage is complete

        for gap in gaps:                                    # targeted re-delegation
            print(f"[coordinator]   re-delegating '{gap}' with a targeted query")
            findings[gap] = delegate(topic, goal, gap, known_so_far=list(findings))
    else:
        print(f"[coordinator] WARNING: hit MAX_ROUNDS={MAX_ROUNDS} with gaps still open: {gaps}")

    return write_report(topic, findings), coverage


# ===========================================================================
# Stubs standing in for real search / a real document store.
# ===========================================================================

_SEARCH_INDEX = {
    "solar":      "Solar PV capacity passed 1,600 GW globally in 2024. Utility-scale PV LCOE ~$0.04/kWh. Perovskite cells are an emerging technology.",
    "wind":       "Onshore wind LCOE ~$0.03-0.05/kWh. Offshore wind is growing fastest; floating turbines open deep-water sites.",
    "geothermal": "Geothermal provides ~16 GW globally. Enhanced geothermal systems (EGS) aim to unlock heat anywhere by drilling deep.",
    "tidal":      "Tidal stream and tidal range plants are predictable but site-limited. MeyGen in Scotland is the largest tidal stream array.",
    "biomass":    "Bioenergy is ~10% of global primary energy. Sustainability depends on feedstock; BECCS pairs it with carbon capture.",
    "fusion":     "Fusion is pre-commercial. ITER targets first plasma in the 2030s; private firms (Commonwealth Fusion, Helion) aim for pilot plants.",
    "hydro":      "Hydropower is the largest renewable source, ~1,400 GW. Pumped storage provides most grid-scale energy storage.",
    "hydrogen":   "Green hydrogen via electrolysis is a renewable-adjacent carrier; costs ~$4-6/kg today vs ~$1-2/kg for grey hydrogen.",
}

_DOCUMENT_CORPUS = {
    "solar":      "[IEA PV Report 2024] Module prices fell 50% in 18 months; grid integration and curtailment are now the binding constraints.",
    "wind":       "[GWEC Global Wind Report] 117 GW added in 2023. Supply-chain and permitting delays are the top risks to 2030 targets.",
    "geothermal": "[DOE GeoVision] EGS could supply 60+ GW in the US by 2050 if drilling costs fall 30%.",
    "biomass":    "[IPCC AR6 WGIII] Bioenergy mitigation potential is bounded by land-use competition and lifecycle emissions accounting.",
    # Deliberately no documents for tidal or fusion — the document spoke must say so, not invent.
}


def search_stub(query: str) -> str:
    hits = [v for k, v in _SEARCH_INDEX.items() if k in query.lower()]
    return "\n".join(f"- {h}" for h in hits) or "- No results found."


def document_corpus(query: str) -> str:
    return "\n".join(v for k, v in _DOCUMENT_CORPUS.items() if k in query.lower())


# ===========================================================================
# Step 6: test with "renewable energy technologies"
# ===========================================================================

REQUIRED = ["solar", "wind", "geothermal", "tidal", "biomass", "fusion"]

if __name__ == "__main__":
    narrow = "--narrow" in sys.argv
    topic = "renewable energy technologies"
    print(f"TOPIC: {topic}   (narrow decomposition: {narrow})\n")

    report, coverage = coordinator(topic, narrow=narrow)

    print("\n" + "=" * 70 + "\nFINAL COVERAGE\n" + "=" * 70)
    for s, status in coverage.items():
        print(f"  {status:8s} {s}")

    print("\n" + "=" * 70 + "\nREPORT\n" + "=" * 70 + "\n" + report)

    missing = [r for r in REQUIRED if r not in report.lower()]
    print("\n" + "=" * 70)
    print("PASS: all six required technologies present." if not missing
          else f"FAIL: report is missing {missing} -> check the coordinator's decomposition first.")
