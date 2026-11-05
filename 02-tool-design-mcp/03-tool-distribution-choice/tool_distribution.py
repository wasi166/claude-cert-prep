"""
Lesson 2.3 — Tool Distribution & Tool Choice

Fifteen tools exist. No agent sees all of them.

    web-search agent        web_search, fetch_snippet, rank_results, extract_entities          (4)
    document-analysis agent load_document, extract_metadata, extract_sections, summarize_section (4)
    synthesis agent         compile_report, cite_sources, check_consistency, verify_fact*       (4)

    * verify_fact is a SCOPED CROSS-ROLE tool: simple single-source checks handled locally,
      complex multi-source checks escalated to the coordinator. Saves 2-3 round trips.

tool_choice, three modes, used for three different reasons:
    {"type": "auto"}                          model decides whether/which    (synthesis: free reasoning)
    {"type": "any"}                           a tool WILL be called          (web-search: must search, not answer from memory)
    {"type": "tool", "name": "extract_metadata"}  THIS tool will be called   (doc-analysis: mandatory first step)

Run:  python tool_distribution.py                 # scoped roles, forced ordering
      python tool_distribution.py --overloaded    # synthesis agent given all 15 tools; watch it misbehave
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

import anthropic
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-sonnet-4-5"
MAX_TURNS = 8
NUM = r"\d[\d,]*(?:\.\d+)?"   # 597 · 1,600 · 0.04 — but not the full stop after 2024.


# ===========================================================================
# The tool catalogue — everything that exists in the system.
# ===========================================================================
def _schema(**props) -> dict:
    return {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": list(props)}


FACTS = {   # a tiny world for the stubs
    "https://docs.example.org/solar-2024.pdf": "Global solar PV additions reached 597 GW in 2024.",
    "https://docs.example.org/wind-2024.pdf":  "Wind added 117 GW in 2024; offshore wind added 11 GW.",
    "https://docs.example.org/costs.md":       "Utility-scale solar LCOE fell below $0.04/kWh in 2024.",
}

# --- web-search role -------------------------------------------------------
def web_search(query: str) -> str:
    hits = [{"url": u, "snippet": t} for u, t in FACTS.items() if any(w in t.lower() for w in query.lower().split())]
    return json.dumps({"resultCount": len(hits), "results": hits})

def fetch_snippet(url: str) -> str:
    return json.dumps({"url": url, "snippet": FACTS.get(url, "")})

def rank_results(urls: list) -> str:
    return json.dumps({"ranked": sorted(urls)})

def extract_entities(text: str) -> str:
    return json.dumps({"numbers": re.findall(r"\$?\d[\d,.]*\s?(?:GW|kWh|%)?", text)})

# --- document-analysis role -------------------------------------------------
DOCUMENT_EXTENSIONS = (".pdf", ".md", ".txt", ".docx")
TRUSTED_DOMAINS = ("docs.example.org", "reports.example.org")

def fetch_url(url: str) -> str:
    """GENERIC — fetches anything. Exists in the catalogue to show what we replaced. Assigned to NO role."""
    return json.dumps({"url": url, "body": FACTS.get(url, "<html>...</html>")})

def load_document(url: str) -> str:
    """CONSTRAINED replacement for fetch_url: document extensions on trusted domains only."""
    p = urlparse(url)
    if p.scheme != "https":
        return _err("validation", f"Refused: only https URLs are accepted, got '{p.scheme}'.")
    if p.netloc not in TRUSTED_DOMAINS:
        return _err("permission", f"Refused: '{p.netloc}' is not a trusted document host. Allowed: {', '.join(TRUSTED_DOMAINS)}.")
    if not p.path.lower().endswith(DOCUMENT_EXTENSIONS):
        return _err("validation", f"Refused: '{p.path}' is not a document. Allowed extensions: {', '.join(DOCUMENT_EXTENSIONS)}.")
    return json.dumps({"url": url, "body": FACTS.get(url, ""), "loaded": True})

def extract_metadata(url: str) -> str:
    return json.dumps({"url": url, "title": urlparse(url).path.rsplit("/", 1)[-1], "year": 2024, "pages": 12})

def extract_sections(url: str) -> str:
    return json.dumps({"sections": ["Summary", "Capacity", "Costs"]})

def summarize_section(text: str) -> str:
    return json.dumps({"summary": text[:120]})

# --- synthesis role -----------------------------------------------------------
def compile_report(findings: list) -> str:
    return json.dumps({"report": "\n".join(f"- {f}" for f in findings)})

def cite_sources(claims: list) -> str:
    return json.dumps({"cited": [{"claim": c, "source": next((u for u, t in FACTS.items() if c[:20] in t), None)} for c in claims]})

def check_consistency(claims: list) -> str:
    return json.dumps({"consistent": True, "conflicts": []})

def verify_fact(claim: str, source_url: str) -> str:
    """SCOPED cross-role tool: one claim, one source. Anything more goes back to the coordinator."""
    text = FACTS.get(source_url)
    if text is None:
        return json.dumps({"verified": False, "escalate": True,
                           "reason": "source not in local store — needs coordinator-level web verification"})
    numbers_in_claim = set(re.findall(NUM, claim))
    return json.dumps({"verified": numbers_in_claim <= set(re.findall(NUM, text)), "source": source_url})


def _err(category: str, description: str) -> str:
    return json.dumps({"isError": True, "errorCategory": category, "isRetryable": False, "description": description})


CATALOGUE = {
    # name: (description, input_schema, handler)
    "web_search":        ("Search the web for a query. Returns URLs and snippets.", _schema(query="string"), web_search),
    "fetch_snippet":     ("Fetch the snippet for one search-result URL.", _schema(url="string"), fetch_snippet),
    "rank_results":      ("Rank a list of result URLs by relevance.", {"type": "object", "properties": {"urls": {"type": "array", "items": {"type": "string"}}}, "required": ["urls"]}, rank_results),
    "extract_entities":  ("Pull numbers and units out of a text snippet.", _schema(text="string"), extract_entities),

    "fetch_url":         ("Fetch the raw contents of ANY URL.", _schema(url="string"), fetch_url),
    "load_document":     (f"Load a document for analysis. Accepts https URLs ending in {', '.join(DOCUMENT_EXTENSIONS)} "
                          f"on trusted hosts ({', '.join(TRUSTED_DOMAINS)}) only; anything else is refused with an error. "
                          "Do NOT use for web pages or APIs.", _schema(url="string"), load_document),
    "extract_metadata":  ("Extract title, year and page count from a loaded document. MUST run before any other analysis of that document.", _schema(url="string"), extract_metadata),
    "extract_sections":  ("List the sections of a loaded document.", _schema(url="string"), extract_sections),
    "summarize_section": ("Summarise one section's text.", _schema(text="string"), summarize_section),

    "compile_report":    ("Assemble a list of findings into a report.", {"type": "object", "properties": {"findings": {"type": "array", "items": {"type": "string"}}}, "required": ["findings"]}, compile_report),
    "cite_sources":      ("Attach a source URL to each claim.", {"type": "object", "properties": {"claims": {"type": "array", "items": {"type": "string"}}}, "required": ["claims"]}, cite_sources),
    "check_consistency": ("Check a list of claims for contradictions.", {"type": "object", "properties": {"claims": {"type": "array", "items": {"type": "string"}}}, "required": ["claims"]}, check_consistency),
    "verify_fact":       ("Verify ONE simple factual claim against ONE already-known source URL (single-source lookup). "
                          "Use for quick checks like 'does source X say 597 GW?'. Do NOT use for claims needing several "
                          "sources, new web research, or judgement calls — return those to the coordinator instead "
                          "(the result will say escalate: true).", _schema(claim="string", source_url="string"), verify_fact),

    "send_email":        ("Send an email.", _schema(to="string", body="string"), lambda **k: "{}"),
    "run_sql":           ("Run a SQL query against the warehouse.", _schema(sql="string"), lambda **k: "{}"),
}


# ===========================================================================
# Step 1 + 2: distribution. Small, scoped, disjoint — except the scoped cross-role tool.
# ===========================================================================
ROLES = {
    "web-search": {
        "tools": ["web_search", "fetch_snippet", "rank_results", "extract_entities"],
        "system": "You are a web research agent. Search for the topic, fetch snippets, and return the facts you found "
                  "with their URLs as a JSON list of {claim, url}. You cannot load documents or write reports.",
        "first_call": {"type": "any"},                            # a search WILL happen; no answering from memory
    },
    "document-analysis": {
        "tools": ["load_document", "extract_metadata", "extract_sections", "summarize_section"],
        "system": "You are a document analysis agent. For each document URL you are given: load it, extract its metadata, "
                  "then summarise. Return a JSON list of {claim, url, page}. You cannot search the web.",
        "first_call": {"type": "tool", "name": "extract_metadata"},   # Step 3: mandatory first step
    },
    "synthesis": {
        "tools": ["compile_report", "cite_sources", "check_consistency", "verify_fact"],
        "system": "You are a synthesis agent. You receive findings from other agents. Check them for consistency, "
                  "verify any single-source numeric claim with verify_fact, cite, and compile a report. You cannot "
                  "search the web or load documents — if a claim needs new research, say so and leave it uncited.",
        "first_call": {"type": "auto"},
    },
}
OVERLOADED_SYNTHESIS = list(CATALOGUE)     # all 15 — the anti-pattern


def tools_for(names: list[str]) -> list[dict]:
    return [{"name": n, "description": CATALOGUE[n][0], "input_schema": CATALOGUE[n][1]} for n in names]


class CrossRoleMisuse(Exception):
    pass


# ===========================================================================
# Running one role: tool_choice on the first call, auto afterwards, audit every call.
# ===========================================================================
def run_role(role: str, prompt: str, tool_names: list[str] | None = None) -> tuple[str, list[str], list[dict]]:
    """Returns (final_text, tools_called_in_order, tool_choice_per_turn)."""
    cfg = ROLES[role]
    names = tool_names or cfg["tools"]
    tools = tools_for(names)
    messages = [{"role": "user", "content": prompt}]
    called, choices = [], []

    for turn in range(MAX_TURNS):
        choice = cfg["first_call"] if turn == 0 else {"type": "auto"}
        choices.append(choice)
        r = client.messages.create(model=MODEL, max_tokens=1200, system=cfg["system"],
                                   tools=tools, tool_choice=choice, messages=messages)
        if r.stop_reason == "end_turn":
            return "".join(b.text for b in r.content if b.type == "text"), called, choices
        if r.stop_reason != "tool_use":
            return f"[{r.stop_reason}]", called, choices

        messages.append({"role": "assistant", "content": r.content})
        results = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            if b.name not in names:                                    # cannot happen via the API; audit anyway
                raise CrossRoleMisuse(f"{role} called {b.name}, which is not in its toolset {names}")
            called.append(b.name)
            out = CATALOGUE[b.name][2](**b.input)
            print(f"    [{role}] {b.name}({json.dumps(b.input)[:60]}) -> {out[:70]}")
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        messages.append({"role": "user", "content": results})
    return "[max turns]", called, choices


# ===========================================================================
# Step 5: a query that needs all three roles. Python is the coordinator.
# ===========================================================================
def pipeline(topic: str, overloaded: bool = False) -> dict:
    log = {}
    print(f"\nTOPIC: {topic}\n")

    print("1. web-search agent")
    text, called, choices = run_role("web-search", f"Find current facts about {topic}.")
    log["web-search"] = {"called": called, "first_choice": choices[0]}
    web_findings = text

    print("\n2. document-analysis agent (extract_metadata forced first)")
    docs = [u for u in FACTS if u.endswith(DOCUMENT_EXTENSIONS)]
    text, called, choices = run_role("document-analysis", f"Analyse these documents about {topic}: {docs}")
    log["document-analysis"] = {"called": called, "first_choice": choices[0]}
    doc_findings = text

    print(f"\n3. synthesis agent ({'ALL 15 TOOLS — overloaded' if overloaded else 'scoped 4 tools'})")
    prompt = (f"Findings from the web-search agent:\n{web_findings}\n\nFindings from the document-analysis agent:\n"
              f"{doc_findings}\n\nProduce a short cited report on {topic}.")
    text, called, choices = run_role("synthesis", prompt, OVERLOADED_SYNTHESIS if overloaded else None)
    log["synthesis"] = {"called": called, "first_choice": choices[0]}
    print(f"\nREPORT:\n{text}\n")
    return log


def audit(log: dict) -> list[str]:
    problems = []
    for role, entry in log.items():
        outside = [t for t in entry["called"] if t not in ROLES[role]["tools"]]
        if outside:
            problems.append(f"{role} used tools outside its role: {outside}")
    da = log["document-analysis"]["called"]
    if not da or da[0] != "extract_metadata":
        problems.append(f"document-analysis did not start with extract_metadata: {da[:3]}")
    if not log["web-search"]["called"]:
        problems.append("web-search agent answered without searching")
    return problems


if __name__ == "__main__":
    overloaded = "--overloaded" in sys.argv
    log = pipeline("renewable energy capacity added in 2024", overloaded=overloaded)
    print("=" * 70 + "\nTOOL USAGE BY ROLE\n" + "=" * 70)
    for role, e in log.items():
        print(f"  {role:18s} first tool_choice={json.dumps(e['first_choice']):40s} called={e['called']}")
    problems = audit(log)
    print("\n" + ("PASS: no cross-role misuse; mandatory first step honoured." if not problems
                  else "FAIL:\n  - " + "\n  - ".join(problems)))
