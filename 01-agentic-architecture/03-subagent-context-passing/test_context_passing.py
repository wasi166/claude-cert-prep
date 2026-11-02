"""
Offline checks. No API key, no SDK subprocess: pytest -q

1. The coordinator can spawn subagents: "Agent" is in allowed_tools, subagents are registered
2. Each subagent's tools are restricted to its role
3. The Finding schema separates content fields from metadata fields
4. The coordinator is instructed to pass findings intact
5. verify_attribution() catches uncited claims
6. is_parallel() distinguishes two Agent calls in one turn from two turns
"""
import context_passing as lesson


def test_coordinator_can_spawn_subagents():
    opts = lesson.build_options(parallel=True)
    assert "Agent" in opts.allowed_tools                       # step 1: binary requirement
    assert set(opts.agents) == {"web-searcher", "doc-analyst", "synthesizer"}


def test_subagent_tools_are_scoped_to_role():
    a = lesson.AGENTS
    assert set(a["web-searcher"].tools) <= {"WebSearch", "WebFetch"}
    assert set(a["doc-analyst"].tools) <= {"Read", "Glob", "Grep"}
    assert a["synthesizer"].tools == []
    for d in a.values():                                       # proper AgentDefinition fields
        assert d.description and d.prompt


def test_finding_schema_separates_content_from_metadata():
    props = lesson.FINDING_SCHEMA["properties"]
    content = {"claim", "analysis"}
    metadata = {"source_url", "document_name", "page_number", "confidence", "retrieved_by"}
    assert content | metadata == set(props)
    assert set(lesson.FINDING_SCHEMA["required"]) == content | metadata   # nothing optional


def test_coordinator_is_told_to_pass_findings_intact():
    p = lesson.coordinator_system_prompt(parallel=True)
    assert "COMPLETE findings" in p and "every field intact" in p
    assert "Do NOT summarise" in p
    for field in ("source_url", "document_name", "page_number"):
        assert field in p                                       # metadata named explicitly


def test_parallel_vs_sequential_instruction():
    assert "SAME response" in lesson.coordinator_system_prompt(parallel=True)
    assert "wait for its result, then" in lesson.coordinator_system_prompt(parallel=False)


def test_verify_attribution_flags_uncited_claims():
    report = """
- Solar additions hit 597 GW in 2024. [source: iea_solar_outlook_2025.md, p.1]
- Offshore wind added 11 GW. [source: https://gwec.net/report-2025]
- Wind is growing fast.
"""
    cited, uncited = lesson.verify_attribution(report)
    assert len(cited) == 2
    assert uncited == ["- Wind is growing fast."]


def test_is_parallel_requires_both_agents_in_one_turn():
    assert lesson.is_parallel([["web-searcher", "doc-analyst"], ["synthesizer"]])
    assert not lesson.is_parallel([["web-searcher"], ["doc-analyst"], ["synthesizer"]])
