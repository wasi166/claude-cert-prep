"""
Lesson 1.1 — Agentic Loops

The whole lesson is one idea: call Claude in a loop, and let `stop_reason`
decide what happens next.

    tool_use  -> run the tool, send the result back, loop again
    end_turn  -> Claude is finished, return its answer

Run:  python agentic_loop.py
      (reads ANTHROPIC_API_KEY from the repo-root .env)
"""

import os

import anthropic
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-5"
MAX_ITERATIONS = 20  # Safety cap only. It should never be the reason the loop ends.


# ---------------------------------------------------------------------------
# Step 1: Two tools.
# Each definition has three parts: name, description, input_schema (JSON Schema).
# The description is what Claude reads to decide WHICH tool to use.
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression like '330 * 3'. Use this for any math.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "The expression to evaluate."}
            },
            "required": ["expression"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for a fact. Returns a short text snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."}
            },
            "required": ["query"],
        },
    },
]


# The API never runs code. It only *asks* for a tool by name.
# These are the functions WE run when it asks.
def calculator(expression: str) -> str:
    if not set(expression) <= set("0123456789.+-*/() "):
        return "error: only numbers and + - * / ( ) are allowed"
    return str(eval(expression))


def web_search(query: str) -> str:
    # A stub, so results are predictable while you learn the loop.
    facts = {
        "eiffel": "The Eiffel Tower is 330 meters tall.",
        "everest": "Mount Everest is 8,849 meters tall.",
    }
    for key, fact in facts.items():
        if key in query.lower():
            return fact
    return "No results found."


def run_tool(name: str, tool_input: dict) -> str:
    if name == "calculator":
        return calculator(**tool_input)
    if name == "web_search":
        return web_search(**tool_input)
    return f"error: unknown tool {name}"


# ---------------------------------------------------------------------------
# Steps 2–4 and 6: The agentic loop.
# ---------------------------------------------------------------------------
def run_agent(user_prompt: str) -> str:
    messages = [{"role": "user", "content": user_prompt}]
    iteration = 0

    while True:
        # Step 6: safety cap. A tripwire for runaway loops, NOT the normal exit.
        iteration += 1
        if iteration > MAX_ITERATIONS:
            print(f"WARNING: hit MAX_ITERATIONS={MAX_ITERATIONS}. The loop should end via stop_reason.")
            return "Agent stopped: iteration cap reached."

        # Step 2: call Claude, then look at stop_reason — nothing else.
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )
        print(f"[iteration {iteration}] stop_reason = {response.stop_reason}")

        # Step 4: Claude says it is finished. Return its text and stop.
        if response.stop_reason == "end_turn":
            return "".join(block.text for block in response.content if block.type == "text")

        # Step 3: Claude wants a tool. Three moves, in this order:
        if response.stop_reason == "tool_use":

            # (a) Put Claude's turn into history exactly as it came back.
            #     It contains the tool_use block(s) with their ids.
            messages.append({"role": "assistant", "content": response.content})

            # (b) Run every tool Claude asked for. Each result is tagged with
            #     the id of the tool_use block it answers.
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = run_tool(block.name, block.input)
                    print(f"    {block.name}({block.input}) -> {result}")
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            # (c) Send the results back as ONE user message, then loop.
            messages.append({"role": "user", "content": tool_results})
            continue

        # Anything else (max_tokens, refusal, ...) — stop and say so rather than loop blindly.
        return f"Stopped with unexpected stop_reason: {response.stop_reason}"


# ---------------------------------------------------------------------------
# Step 5: A prompt that needs two tools in sequence — search, then calculate.
# Expect: tool_use (web_search) -> tool_use (calculator) -> end_turn.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    prompt = (
        "Find the height of the Eiffel Tower, then calculate how tall "
        "three of them stacked on top of each other would be."
    )
    print(f"USER: {prompt}\n")
    answer = run_agent(prompt)
    print(f"\nCLAUDE: {answer}")
