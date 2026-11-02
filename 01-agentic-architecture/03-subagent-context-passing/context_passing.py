"""
Lesson 1.3 — Subagent Invocation and Context Passing (Claude Agent SDK)

Lesson 1.2 built a coordinator by hand with raw Messages API calls. This lesson uses the
Claude Agent SDK, where the coordinator is itself a Claude session that spawns subagents by
calling the built-in `Agent` tool. Our code's job is to CONFIGURE the coordinator correctly:

    1. allowed_tools must include "Agent"      -> otherwise no subagent can be spawned
    2. subagents are AgentDefinitions           -> description, prompt, restricted tools
    3. findings carry structured metadata       -> claim + source_url + document_name + page + confidence
    4. the coordinator passes findings INTACT   -> to the synthesis subagent
    5. verify every claim in the report is cited
    6. independent subagents run IN PARALLEL    -> two Agent calls in one coordinator turn

Run:  python context_passing.py                 # parallel (the correct pattern)
      python context_passing.py --sequential    # same thing, one subagent at a time, for comparison
Needs: pip install claude-agent-sdk    and ANTHROPIC_API_KEY in the repo-root .env
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition, AssistantMessage, ClaudeAgentOptions, ResultMessage,
    TextBlock, ToolUseBlock, query,
)
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
HERE = Path(__file__).parent

# ===========================================================================
# Step 3: Structured output. Content and metadata are separate fields, so the
# synthesis agent can cite the metadata without parsing it out of prose.
# ===========================================================================
FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        # --- content ---
        "claim":         {"type": "string", "description": "One factual statement."},
        "analysis":      {"type": "string", "description": "Why it matters, 1 sentence."},
        # --- metadata (source attribution) ---
        "source_url":    {"type": ["string", "null"], "description": "URL the claim came from, if web."},
        "document_name": {"type": ["string", "null"], "description": "File the claim came from, if document."},
        "page_number":   {"type": ["integer", "null"], "description": "Page in that document, if document."},
        "confidence":    {"type": "number", "minimum": 0, "maximum": 1},
        "retrieved_by":  {"type": "string", "enum": ["web-searcher", "doc-analyst"]},
    },
    "required": ["claim", "analysis", "source_url", "document_name", "page_number", "confidence", "retrieved_by"],
}

FINDINGS_INSTRUCTION = (
    "Return ONLY a JSON array of findings. Each finding must match this schema exactly, "
    "with every field present (use null where not applicable):\n"
    + json.dumps(FINDING_SCHEMA, indent=2)
)

# ===========================================================================
# Step 2: Two research subagents + one synthesis subagent.
# Each has a description (how the coordinator picks it), a prompt (its system
# prompt), and `tools` restricted to its role.
# ===========================================================================
AGENTS = {
    "web-searcher": AgentDefinition(
        description="Searches the web for current facts and returns findings with source URLs.",
        prompt=(
            "You are a web research subagent. Use WebSearch (and WebFetch if needed) to find "
            "3-4 current, specific facts about the assigned topic. For each, record the exact "
            "page URL in source_url. Set document_name and page_number to null, "
            "retrieved_by to 'web-searcher'.\n\n" + FINDINGS_INSTRUCTION
        ),
        tools=["WebSearch", "WebFetch"],          # search only — no file access
        model="sonnet",
    ),
    "doc-analyst": AgentDefinition(
        description="Reads local documents in ./docs and returns findings with document name and page number.",
        prompt=(
            "You are a document analysis subagent. Use Glob to list ./docs/*.md, then Read each "
            "file. Documents contain '--- page N ---' markers. Extract 3-4 findings relevant to "
            "the assigned topic. For each, record document_name (the file name) and page_number "
            "(from the marker above the text). Set source_url to null, retrieved_by to "
            "'doc-analyst'.\n\n" + FINDINGS_INSTRUCTION
        ),
        tools=["Read", "Glob", "Grep"],           # file reading only — no web access
        model="sonnet",
    ),
    "synthesizer": AgentDefinition(
        description="Writes a cited report from a JSON array of findings.",
        prompt=(
            "You are a synthesis subagent. You receive a JSON array of findings with metadata. "
            "Write a short report as bullet points. EVERY bullet must state one claim and end "
            "with a citation built from that finding's metadata:\n"
            "  web finding  -> [source: <source_url>]\n"
            "  doc finding  -> [source: <document_name>, p.<page_number>]\n"
            "Never write a bullet without a citation. Never invent a source."
        ),
        tools=[],                                 # pure reasoning — no tools at all
        model="sonnet",
    ),
}

# ===========================================================================
# Step 1 + 4 + 6: The coordinator. It is a Claude session; we steer it with a
# system prompt and give it the Agent tool so it can spawn the subagents.
# ===========================================================================
def coordinator_system_prompt(parallel: bool) -> str:
    spawn_rule = (
        "Invoke web-searcher AND doc-analyst in the SAME response (two Agent tool calls at once) "
        "because they are independent. Wait for both results before continuing."
        if parallel else
        "Invoke web-searcher first, wait for its result, then invoke doc-analyst."
    )
    return f"""You are a research coordinator. You do no research yourself; you delegate.

Workflow:
1. {spawn_rule}
2. Collect the JSON findings arrays both subagents return.
3. Invoke synthesizer ONCE. Its prompt must contain the COMPLETE findings from both subagents,
   as raw JSON, with every field intact (claim, analysis, source_url, document_name,
   page_number, confidence, retrieved_by). Do NOT summarise, paraphrase, or strip any field.
4. Return the synthesizer's report verbatim as your final answer.
"""


def build_options(parallel: bool) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=coordinator_system_prompt(parallel),
        agents=AGENTS,
        # "Agent" is the tool that spawns subagents (named "Task" before Claude Code v2.1.63;
        # "Task" still works as an alias). Without it here, the coordinator cannot delegate.
        allowed_tools=["Agent"],
        permission_mode="bypassPermissions",
        cwd=str(HERE),
        max_turns=20,
    )


# ===========================================================================
# Observing the stream: which subagents were spawned, and were they parallel?
# ===========================================================================
async def run_coordinator(topic: str, parallel: bool) -> tuple[str, list[list[str]]]:
    """Returns (final_report, spawn_batches). Each batch = subagents spawned in ONE coordinator turn."""
    spawn_batches: list[list[str]] = []
    report = ""
    t0 = time.perf_counter()

    async for message in query(prompt=f"Research topic: {topic}", options=build_options(parallel)):
        if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            # A coordinator turn (parent_tool_use_id is set for messages *inside* a subagent).
            spawned = [b.input.get("subagent_type") for b in message.content
                       if isinstance(b, ToolUseBlock) and b.name in ("Agent", "Task")]
            if spawned:
                spawn_batches.append(spawned)
                print(f"[{time.perf_counter() - t0:5.1f}s] coordinator spawned {spawned}"
                      + ("  <- parallel" if len(spawned) > 1 else ""))
        elif isinstance(message, ResultMessage):
            report = message.result or ""
            print(f"[{time.perf_counter() - t0:5.1f}s] done. turns={message.num_turns} "
                  f"cost=${message.total_cost_usd or 0:.4f}")

    return report, spawn_batches


# ===========================================================================
# Step 5: Verify attribution. Every bullet must carry a citation.
# ===========================================================================
CITATION = re.compile(r"\[source: (https?://\S+?|[^\]]+?, p\.\d+)\]")


def verify_attribution(report: str) -> tuple[list[str], list[str]]:
    """Returns (cited_bullets, uncited_bullets)."""
    bullets = [ln.strip() for ln in report.splitlines() if ln.strip().startswith(("-", "*", "•"))]
    cited = [b for b in bullets if CITATION.search(b)]
    uncited = [b for b in bullets if not CITATION.search(b)]
    return cited, uncited


def is_parallel(spawn_batches: list[list[str]]) -> bool:
    """True if web-searcher and doc-analyst were spawned in the same coordinator turn."""
    return any({"web-searcher", "doc-analyst"} <= set(batch) for batch in spawn_batches)


# ===========================================================================
if __name__ == "__main__":
    parallel = "--sequential" not in sys.argv
    topic = "the current state of solar and wind energy deployment"
    print(f"TOPIC: {topic}\nMODE:  {'parallel' if parallel else 'sequential'}\n")

    report, batches = asyncio.run(run_coordinator(topic, parallel))

    print("\n" + "=" * 70 + "\nREPORT\n" + "=" * 70 + "\n" + report)

    cited, uncited = verify_attribution(report)
    print("\n" + "=" * 70 + "\nVERIFICATION\n" + "=" * 70)
    print(f"spawn batches : {batches}  ->  {'PARALLEL' if is_parallel(batches) else 'SEQUENTIAL'}")
    print(f"cited claims  : {len(cited)}")
    print(f"uncited claims: {len(uncited)}")
    for u in uncited:
        print(f"    !! {u}")
    if uncited:
        print("\nFAIL: uncited claims. Trace back: did the coordinator pass the findings JSON "
              "to the synthesizer with metadata intact? (Check that before blaming the synthesizer.)")
    else:
        print("\nPASS: every claim is attributed to a URL or a document+page.")
