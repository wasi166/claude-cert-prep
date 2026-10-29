# Claude Certification Prep

Hands-on projects built while studying for the Claude certification exam. Each lesson is a
self-contained folder with runnable Python, offline tests, and a README that walks through the
code step by step and lists the key takeaways and exam traps.

## Lessons

| #  | Lesson | Concepts | Status |
|----|--------|----------|--------|
| 01 | [The Agentic Loop](01-agentic-loop/) | `stop_reason` control flow, `tool_use` / `tool_result`, multi-tool selection, safety caps | ✅ |

## Setup

```bash
git clone <this repo>
cd claude-cert-prep
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then put your key in .env
```

`.env` is gitignored. Every lesson reads `ANTHROPIC_API_KEY` from the repo-root `.env`, so you set it once.

Or open the repo in a **GitHub Codespace** — `.devcontainer/` installs everything automatically.

## Running a lesson

```bash
cd 01-agentic-loop
python agentic_loop.py                   # built-in test prompt
python agentic_loop.py "your prompt"     # custom prompt
```

## Tests

Each lesson ships offline tests for the parts that don't need an API call (tool handlers,
error paths, loop invariants). Run them all from the root:

```bash
pytest -q
```

CI runs the same command on every push (`.github/workflows/tests.yml`).

## Repo conventions

- Folders are numbered `NN-topic/` so they sort in course order.
- Every lesson README follows the same shape: *What this builds · How to run · Code walkthrough · Key takeaways · Exam traps*.
- Tool inputs are treated as untrusted. No `eval()`, no shelling out.
- Errors inside tools are returned to the model as `tool_result` with `is_error: true`, never raised.
