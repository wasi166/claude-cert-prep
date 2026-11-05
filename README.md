# Claude Certified Architect – Foundations (CCAR-F) · Prep Repo

Hands-on projects built while preparing for Anthropic's **Claude Certified Architect – Foundations**
exam. Every lesson in the curriculum gets its own folder with runnable Python, offline tests, and a
README that walks through the code step by step and ends with the key takeaways and exam traps.

Curriculum source: [claudecertificationguide.com/ccar-f](https://claudecertificationguide.com/ccar-f)

## About the certification

CCAR-F is the entry point to Anthropic's Architect track. It is aimed at solution architects and
technical practitioners who design Claude-powered agentic systems, and it tests whether you can
design a *production* Claude system: how an agentic loop terminates, when and how to use subagents,
what goes in an MCP server, how CLAUDE.md is configured, how to handle context-window limits.
It is not an entry-level exam — the credential is awarded on exam performance alone.

| | |
|---|---|
| Exam code | CCAR-F (v1.0, effective July 2026) |
| Format | 60 items, multiple-choice and multiple-response |
| Duration | 120 minutes |
| Passing score | 720 on a 100–1,000 scaled range |
| Delivery | Pearson VUE — online proctored or test centre |
| Cost | $125 USD per attempt |
| Prerequisites | None |
| Validity | 12 months; free on-time renewal via a non-proctored assessment |
| Retakes | 14 / 30 / 90-day waits after failed attempts, max 4 per rolling 12 months |

## The five domains

The exam is split into 5 domains and 30 lessons. Weightings are the share of exam items.

| # | Domain | Weight | Lessons | Folder |
|---|--------|-------:|--------:|--------|
| 1 | Agentic Architecture & Orchestration | 27% | 7 | [`01-agentic-architecture/`](01-agentic-architecture/) |
| 2 | Tool Design & MCP Integration | 18% | 5 | [`02-tool-design-mcp/`](02-tool-design-mcp/) |
| 3 | Claude Code Configuration & Workflows | 20% | 6 | `03-claude-code-config/` |
| 4 | Prompt Engineering & Structured Output | 20% | 6 | `04-prompt-engineering/` |
| 5 | Context Management & Reliability | 15% | 6 | `05-context-reliability/` |

## Progress

### Domain 1 — Agentic Architecture & Orchestration (27%)
- [x] 1.1 [Agentic Loops](01-agentic-architecture/01-agentic-loops/) — `stop_reason` control flow, `tool_use`/`tool_result`, safety caps
- [x] 1.2 [Multi-Agent Orchestration](01-agentic-architecture/02-multi-agent-orchestration/) — hub-and-spoke coordinator, subagent isolation, broad decomposition, refinement loop
- [x] 1.3 [Subagent Invocation and Context Passing](01-agentic-architecture/03-subagent-context-passing/) — Agent SDK subagents, `allowed_tools=["Agent"]`, structured metadata, parallel spawning
- [x] 1.4 [Workflow Enforcement and Handoff](01-agentic-architecture/04-workflow-enforcement-handoff/) — prerequisite gate in code, session state, structured handoff protocol, multi-concern decomposition
- [x] 1.5 [Agent SDK Hooks](01-agentic-architecture/05-agent-sdk-hooks/) — PreToolUse deny vs PostToolUse rewrite, normalisation, threshold + prerequisite policies
- [x] 1.6 [Task Decomposition Strategies](01-agentic-architecture/06-task-decomposition/) — attention dilution, single vs multi-pass review, per-item + cross-item passes, dilution artefacts
- [x] 1.7 [Session State and Resumption](01-agentic-architecture/07-session-state-resumption/) — resume vs fork vs fresh+summary, stale context, targeted re-analysis

### Domain 2 — Tool Design & MCP Integration (18%)
- [x] 2.1 [Tool Interface Design](02-tool-design-mcp/01-tool-interface-design/) — descriptions as the router, five elements, misrouting measurement, system-prompt conflicts
- [x] 2.2 [Structured Error Responses](02-tool-design-mcp/02-structured-error-responses/) — isError, errorCategory/isRetryable/description, valid-empty vs failure, category→action recovery
- [x] 2.3 [Tool Distribution & Tool Choice](02-tool-design-mcp/03-tool-distribution-choice/) — scoped toolsets per role, scoped cross-role tool, tool_choice auto/any/forced, least privilege
- [ ] 2.4 MCP Server Integration
- [ ] 2.5 Built-in Tools

### Domain 3 — Claude Code Configuration & Workflows (20%)
- [ ] 3.1 CLAUDE.md Hierarchy, Scoping, and Modular Organisation
- [ ] 3.2 Custom Slash Commands and Skills
- [ ] 3.3 Path-Specific Rules for Conditional Convention Loading
- [ ] 3.4 Plan Mode vs Direct Execution
- [ ] 3.5 Iterative Refinement Techniques
- [ ] 3.6 CI/CD Integration

### Domain 4 — Prompt Engineering & Structured Output (20%)
- [ ] 4.1 System Prompts with Explicit Criteria
- [ ] 4.2 Few-Shot Prompting
- [ ] 4.3 Structured Output with Tool Use
- [ ] 4.4 Validation, Retry, and Feedback Loops
- [ ] 4.5 Batch Processing Strategies
- [ ] 4.6 Multi-Instance and Multi-Pass Review

### Domain 5 — Context Management & Reliability (15%)
- [ ] 5.1 Context Window Management
- [ ] 5.2 Escalation & Ambiguity Resolution
- [ ] 5.3 Error Propagation in Multi-Agent Systems
- [ ] 5.4 Codebase Exploration & Context Degradation
- [ ] 5.5 Human Review & Confidence Calibration
- [ ] 5.6 Information Provenance & Multi-Source Synthesis

## How the repo is structured

```
claude-cert-prep/
├── README.md                    ← this file: exam overview, domain map, progress
├── requirements.txt             ← anthropic, claude-agent-sdk, python-dotenv, pytest
├── .env.example                 ← copy to .env and add ANTHROPIC_API_KEY (gitignored)
├── .devcontainer/               ← open in GitHub Codespaces, everything installs itself
├── .github/workflows/tests.yml  ← runs `pytest -q` on every push
│
├── 01-agentic-architecture/     ← one folder per exam domain
│   ├── 01-agentic-loops/        ← one folder per lesson
│   │   ├── README.md            ← objectives · problem statement · walkthrough · takeaways · exam traps
│   │   ├── agentic_loop.py      ← THE LESSON: short, heavily commented, real API client
│   │   ├── test_agentic_loop.py ← offline tests for the lesson's core ideas
│   │   └── hardened_version.py  ← optional: same idea with production concerns added
│   ├── 02-multi-agent-orchestration/
│   └── ...
├── 02-tool-design-mcp/
├── 03-claude-code-config/
├── 04-prompt-engineering/
└── 05-context-reliability/
```

Folder numbers match the domain and lesson numbers in the curriculum, so `03-claude-code-config/04-plan-mode/`
is Domain 3, Lesson 4. Every lesson folder is self-contained: you can read its README and run its
code without looking at any other lesson.

### Lesson README template

Every lesson README has the same sections, so they're skimmable the night before the exam:

1. **Learning objectives** — what the curriculum says you should be able to explain afterwards
2. **Problem statement** — the exercise's numbered requirements as given, each with *why it matters*, *what you should see*, and *where in the code* it's satisfied
3. **What this builds** — the resulting project and its output when run
4. **How to run**
5. **Code walkthrough** — step by step, each section's *what* and *why*
6. **Key takeaways** — the 5–7 points the lesson exists to teach
7. **Exam traps** — a table of anti-patterns and why each fails

## Setup

```bash
git clone https://github.com/wasi166/claude-cert-prep.git
cd claude-cert-prep
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then put your key in .env
```

`.env` lives at the repo root and every lesson finds it automatically, so you set the key once.
Or open the repo in a **GitHub Codespace** and `.devcontainer/` does all of this for you.

## Running and testing

```bash
# run a lesson
cd 01-agentic-architecture/01-agentic-loops
python agentic_loop.py                   # built-in test prompt
python agentic_loop.py "your prompt"     # custom prompt

# run every offline test in the repo (no API key needed)
pytest -q
```

## Conventions

- **The lesson file teaches one thing.** It's as short as the concept allows, comments explain *why*,
  and nothing is in it that the exam doesn't test. Production concerns (retries, safe eval, structured
  errors, exit codes) live in an optional `hardened_version.py` when they're worth showing.
- Real API client in every lesson — no mocks in the main code path. Tests mock only the client.
- Every behaviour-governing constant lives at the top of the file with a comment on *why*.
