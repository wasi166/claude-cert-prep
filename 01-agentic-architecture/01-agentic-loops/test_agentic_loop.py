"""
Offline checks for the lesson's three core ideas. No API key needed: pytest -q

1. tool_use  -> tool runs, result goes back with the matching tool_use_id, loop continues
2. end_turn  -> loop exits with the text
3. the cap   -> only fires if end_turn never comes
"""
import os
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
import agentic_loop as lesson  # noqa: E402


def fake_client(responses):
    """A stand-in client that replays scripted responses and records what it was sent."""
    sent = []

    def create(**kwargs):
        sent.append(kwargs["messages"])
        return responses.pop(0)

    return NS(messages=NS(create=create)), sent


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def test_sequential_tool_calls_then_end_turn(monkeypatch):
    client, sent = fake_client([
        NS(stop_reason="tool_use", content=[tool_use("t1", "web_search", query="eiffel height")]),
        NS(stop_reason="tool_use", content=[tool_use("t2", "calculator", expression="330 * 3")]),
        NS(stop_reason="end_turn", content=[text("990 meters.")]),
    ])
    monkeypatch.setattr(lesson, "client", client)

    assert lesson.run_agent("how tall are 3 eiffel towers?") == "990 meters."
    assert len(sent) == 3  # two tool iterations + the final one

    # The history sent on the 3rd call must contain both tool_results, ids matching.
    history = sent[2]
    results = [m for m in history if m["role"] == "user" and isinstance(m["content"], list)]
    assert [r["content"][0]["tool_use_id"] for r in results] == ["t1", "t2"]
    assert results[0]["content"][0]["content"] == "The Eiffel Tower is 330 meters tall."
    assert results[1]["content"][0]["content"] == "990"


def test_assistant_turn_is_appended_verbatim(monkeypatch):
    first = NS(stop_reason="tool_use", content=[text("Let me check."), tool_use("t1", "calculator", expression="1+1")])
    client, sent = fake_client([first, NS(stop_reason="end_turn", content=[text("2")])])
    monkeypatch.setattr(lesson, "client", client)

    lesson.run_agent("1+1?")
    assistant_turns = [m for m in sent[1] if m["role"] == "assistant"]
    assert assistant_turns[0]["content"] is first.content  # not filtered, not copied


def test_cap_only_fires_when_end_turn_never_comes(monkeypatch):
    forever = [NS(stop_reason="tool_use", content=[tool_use(f"t{i}", "calculator", expression="1+1")])
               for i in range(lesson.MAX_ITERATIONS + 5)]
    client, sent = fake_client(forever)
    monkeypatch.setattr(lesson, "client", client)

    assert "iteration cap" in lesson.run_agent("loop forever")
    assert len(sent) == lesson.MAX_ITERATIONS  # made exactly 20 calls, then stopped
