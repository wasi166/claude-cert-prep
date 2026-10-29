"""Offline tests for the tool layer. No API key needed: pytest -q"""
import json
import pytest
import agentic_loop as m


def test_calculator_basic():
    assert m._calculator("330 * 3") == "990"
    assert m._calculator("(330 * 3) / 2") == "495"
    assert m._calculator("2 ** 10") == "1024"


def test_calculator_rejects_code_execution():
    with pytest.raises(ValueError):
        m._calculator("__import__('os').system('echo pwned')")


def test_registry_returns_errors_instead_of_raising():
    out, is_err = m.TOOLS.execute("calculator", {"expression": "330 x 3"})
    assert is_err and "SyntaxError" in out

    out, is_err = m.TOOLS.execute("calculator", {"expr": "1+1"})  # wrong arg name
    assert is_err and "Invalid arguments" in out

    out, is_err = m.TOOLS.execute("does_not_exist", {})
    assert is_err and "Unknown tool" in out


def test_web_search_stub_returns_json():
    out, is_err = m.TOOLS.execute("web_search", {"query": "Eiffel tower height"})
    assert not is_err
    data = json.loads(out)
    assert data["results"] and "330" in data["results"][0]

    out, _ = m.TOOLS.execute("web_search", {"query": "nothing here"})
    assert json.loads(out)["results"] == []


def test_tool_params_have_required_fields():
    for p in m.TOOLS.params():
        assert set(p) == {"name", "description", "input_schema"}
        assert p["input_schema"]["type"] == "object"
        assert "handler" not in p


def test_safety_cap_raises_not_returns():
    class LoopingClient:
        """Fake client that always asks for a tool -> loop never sees end_turn."""
        class messages:
            @staticmethod
            def create(**kw):
                from types import SimpleNamespace as NS
                return NS(
                    stop_reason="tool_use",
                    usage=NS(input_tokens=1, output_tokens=1),
                    content=[m.ToolUseBlock(type="tool_use", id="t1", name="calculator",
                                            input={"expression": "1+1"})],
                )

    agent = m.Agent(LoopingClient(), m.TOOLS, max_iterations=3)
    with pytest.raises(m.IterationCapExceeded):
        agent.run("loop forever")
