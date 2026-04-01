"""LLM prompts should target AI-generated-code pitfalls."""

from app.services.prelaunch import agents_gonogo


def test_analyzer_prompt_mentions_ai_pitfalls_focus():
    s = agents_gonogo.SYSTEM_ANALYZER
    assert "AI" in s
    assert "ownership" in s
    assert "敏感字段" in s
    assert "SSRF" in s
    assert "路径穿越" in s
    assert "stack" in s or "stack trace" in s
    assert "zip" in s.lower()


def test_reporter_prompt_mentions_required_sections():
    s = agents_gonogo.SYSTEM_REPORTER
    assert "ownership" in s
    assert "敏感字段" in s
    assert "幂等" in s or "竞态" in s
    assert "rule_id" in s


def test_domain_prompts_require_rule_id_when_possible():
    for s in (
        agents_gonogo.SYSTEM_AUTHZ,
        agents_gonogo.SYSTEM_SENSITIVE,
        agents_gonogo.SYSTEM_INJECTION,
        agents_gonogo.SYSTEM_OPS,
        agents_gonogo.SYSTEM_DEPENDENCY,
    ):
        assert "rule_id" in s
