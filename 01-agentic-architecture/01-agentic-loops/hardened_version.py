"""
OPTIONAL READING — a production-hardened version of agentic_loop.py.

Start with agentic_loop.py; it teaches the lesson. Come back here afterwards to see
what changes when the same loop has to survive in production:

  - Tool registry (schema + handler kept together) instead of if/elif dispatch
  - Tool failures returned to the model as tool_result with is_error=True, never raised
  - AST-based calculator instead of eval() — model inputs are untrusted
  - Every stop_reason handled explicitly (max_tokens, refusal, unknown)
  - Safety cap raises an exception; main() maps failures to distinct exit codes
  - Token usage and tool-call counts returned in a result object
  - SDK retries/timeouts configured on the client

None of this changes the loop itself. stop_reason still drives everything.

Usage:
    python hardened_version.py                    # built-in multi-step prompt
    python hardened_version.py "your prompt"
    python hardened_version.py -v "your prompt"   # verbose logging
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic
from dotenv import find_dotenv, load_dotenv
from anthropic.types import Message, MessageParam, TextBlock, ToolParam, ToolUseBlock

# =============================================================================
# Configuration
# =============================================================================

# Load ANTHROPIC_API_KEY from the nearest .env walking up from this file (repo root).
load_dotenv(find_dotenv())

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2048
MAX_ITERATIONS = 20          # Safety cap only. stop_reason is the real stopping mechanism.
API_MAX_RETRIES = 3          # SDK-level retries for 429 / 5xx / connection errors.
API_TIMEOUT_SECONDS = 60.0

SYSTEM_PROMPT = (
    "You are a precise assistant with access to tools. Use the web_search tool for "
    "facts you don't know and the calculator tool for any arithmetic. Keep answers brief."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent")


# =============================================================================
# Tool registry
# =============================================================================

@dataclass(frozen=True)
class Tool:
    """A tool = the schema Claude sees + the Python callable that executes it."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., str]

    def to_param(self) -> ToolParam:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def params(self) -> list[ToolParam]:
        return [t.to_param() for t in self._tools.values()]

    def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """
        Run a tool and return (result_text, is_error).

        Errors are *returned*, not raised: the model should see them as tool_result
        content so it can recover (retry with corrected input, pick another tool, or
        explain the failure). Raising would crash the loop and lose the whole run.
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name!r}", True
        try:
            return tool.handler(**tool_input), False
        except TypeError as e:  # bad/missing args from the model
            return f"Invalid arguments for {name}: {e}", True
        except Exception as e:  # noqa: BLE001 - we want to surface anything to the model
            log.warning("Tool %s failed: %s: %s", name, type(e).__name__, e)
            return f"{type(e).__name__}: {e}", True


# -----------------------------------------------------------------------------
# Tool implementations
# -----------------------------------------------------------------------------

def _calculator(expression: str) -> str:
    """Safely evaluate basic arithmetic using Python's AST (no eval())."""
    import ast
    import operator as op

    ops = {
        ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
        ast.Div: op.truediv, ast.Pow: op.pow, ast.Mod: op.mod,
        ast.USub: op.neg, ast.UAdd: op.pos,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsupported syntax: {ast.dump(node)}")

    tree = ast.parse(expression, mode="eval")
    result = _eval(tree)
    # Render ints cleanly (990 rather than 990.0)
    return str(int(result)) if float(result).is_integer() else str(result)


def _web_search(query: str) -> str:
    """
    Stub search backend. Replace the body with a real provider (Brave, Tavily, SerpAPI...)
    — the loop and the tool schema stay exactly the same.
    """
    index = {
        "eiffel": "The Eiffel Tower in Paris is 330 meters (1,083 ft) tall including antennas.",
        "everest": "Mount Everest's summit is 8,849 meters (29,032 ft) above sea level.",
        "burj": "Burj Khalifa in Dubai is 828 meters (2,717 ft) tall.",
        "python": "Python 3.0 was released on December 3, 2008.",
    }
    q = query.lower()
    hits = [snippet for key, snippet in index.items() if key in q]
    if not hits:
        return json.dumps({"query": query, "results": []})
    return json.dumps({"query": query, "results": hits})


TOOLS = ToolRegistry([
    Tool(
        name="calculator",
        description=(
            "Evaluate an arithmetic expression and return the numeric result. "
            "Supports + - * / ** % and parentheses. Use this for ALL math."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A pure arithmetic expression, e.g. '(330 * 3) / 2'.",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        handler=_calculator,
    ),
    Tool(
        name="web_search",
        description=(
            "Search the web for current factual information. Returns a JSON object "
            "with a list of result snippets. Use this when you need a fact you are not sure of."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A concise search query."}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_web_search,
    ),
])


# =============================================================================
# Agent
# =============================================================================

class IterationCapExceeded(RuntimeError):
    """Raised when the safety cap trips. Should never happen in normal operation."""


@dataclass
class AgentRun:
    """Everything we know about a completed run — useful for logging/metrics/tests."""
    final_text: str
    stop_reason: str
    iterations: int
    tool_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    messages: list[MessageParam] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        tools: ToolRegistry,
        *,
        model: str = MODEL,
        system: str = SYSTEM_PROMPT,
        max_tokens: int = MAX_TOKENS,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self.client = client
        self.tools = tools
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations

    # -- single API call -----------------------------------------------------
    def _call_model(self, messages: list[MessageParam]) -> Message:
        # The SDK already retries transient failures (max_retries on the client).
        # We let hard failures (auth, bad request) propagate — they are bugs, not
        # something the loop should paper over.
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=self.tools.params(),
            messages=messages,
        )

    # -- the agentic loop ----------------------------------------------------
    def run(self, user_prompt: str) -> AgentRun:
        messages: list[MessageParam] = [{"role": "user", "content": user_prompt}]
        iterations = 0
        tool_calls = 0
        in_tok = out_tok = 0
        t0 = time.perf_counter()

        while True:
            # --- Safety cap: a tripwire for runaway loops, NOT the exit condition.
            iterations += 1
            if iterations > self.max_iterations:
                log.warning(
                    "Safety cap hit (MAX_ITERATIONS=%d) after %d tool calls. "
                    "This indicates a bug or an adversarial/looping task.",
                    self.max_iterations, tool_calls,
                )
                raise IterationCapExceeded(
                    f"Exceeded {self.max_iterations} iterations without end_turn"
                )

            response = self._call_model(messages)
            in_tok += response.usage.input_tokens
            out_tok += response.usage.output_tokens
            log.info("iter=%d stop_reason=%s", iterations, response.stop_reason)

            # --- stop_reason is the ONLY thing that drives control flow. -----

            if response.stop_reason == "tool_use":
                # 1) Echo the assistant turn verbatim so tool_use ids are in history.
                messages.append({"role": "assistant", "content": response.content})

                # 2) Execute every tool_use block (Claude may request several at once).
                results: list[dict[str, Any]] = []
                for block in response.content:
                    if not isinstance(block, ToolUseBlock):
                        continue
                    tool_calls += 1
                    log.info("  -> %s(%s)", block.name, json.dumps(block.input))
                    output, is_error = self.tools.execute(block.name, block.input)
                    log.info("  <- %s%s", "ERROR: " if is_error else "", output)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    })

                # 3) All results go back in ONE user message.
                messages.append({"role": "user", "content": results})
                continue

            if response.stop_reason == "end_turn":
                final_text = "".join(
                    b.text for b in response.content if isinstance(b, TextBlock)
                )
                log.info(
                    "done: %d iterations, %d tool calls, %d in / %d out tokens, %.1fs",
                    iterations, tool_calls, in_tok, out_tok, time.perf_counter() - t0,
                )
                return AgentRun(
                    final_text=final_text,
                    stop_reason=response.stop_reason,
                    iterations=iterations,
                    tool_calls=tool_calls,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    messages=messages,
                )

            if response.stop_reason == "max_tokens":
                # The response was truncated. In production you'd either raise
                # max_tokens or re-prompt for continuation. We surface it clearly.
                log.warning("Response truncated by max_tokens=%d", self.max_tokens)
                partial = "".join(b.text for b in response.content if isinstance(b, TextBlock))
                return AgentRun(partial, "max_tokens", iterations, tool_calls, in_tok, out_tok, messages)

            if response.stop_reason == "refusal":
                log.warning("Model refused the request")
                return AgentRun("", "refusal", iterations, tool_calls, in_tok, out_tok, messages)

            # Any other value (stop_sequence, pause_turn, future additions) is handled
            # explicitly rather than silently looping.
            raise RuntimeError(f"Unhandled stop_reason: {response.stop_reason!r}")


# =============================================================================
# Entry point
# =============================================================================

def build_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.exit("No API key. Copy .env.example to .env at the repo root and set ANTHROPIC_API_KEY.")
    return anthropic.Anthropic(
        api_key=api_key,
        max_retries=API_MAX_RETRIES,
        timeout=API_TIMEOUT_SECONDS,
    )


DEFAULT_PROMPT = (
    "Find the height of the Eiffel Tower, then calculate how tall three of them "
    "stacked end to end would be. Show the number in meters."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the agentic loop against Claude.")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-level logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    agent = Agent(client=build_client(), tools=TOOLS)

    print(f"\nUSER: {args.prompt}\n")
    try:
        run = agent.run(args.prompt)
    except IterationCapExceeded as e:
        print(f"ABORTED: {e}", file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        print("ABORTED: invalid API key", file=sys.stderr)
        return 3
    except anthropic.APIStatusError as e:
        print(f"ABORTED: API error {e.status_code}: {e.message}", file=sys.stderr)
        return 4

    print(f"ASSISTANT: {run.final_text}\n")
    print(
        f"[stop_reason={run.stop_reason} iterations={run.iterations} "
        f"tool_calls={run.tool_calls} tokens={run.input_tokens}+{run.output_tokens}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
