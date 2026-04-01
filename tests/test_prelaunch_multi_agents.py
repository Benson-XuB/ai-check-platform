"""Prelaunch multi-domain LLM agents orchestration."""

from pathlib import Path

from app.services.prelaunch.schemas import NormalizedFinding


def test_multi_agent_pipeline_calls_multiple_domains(monkeypatch, tmp_path: Path):
    from app.services.prelaunch import agents_gonogo
    from app.services.prelaunch.detect import ProjectProfile

    calls = []

    def fake_llm_chat_json(system: str, user: str, llm_provider: str, api_key: str, *, max_tokens: int = 0):
        calls.append(system[:40])
        # Return minimal valid shapes depending on which "system prompt" it is.
        if "AuthZ" in system or "ownership" in system:
            return {"issues": [{"id": "F1", "title": "Missing ownership check", "severity": "High", "category": "sast"}]}
        if "Sensitive" in system or "敏感字段" in system:
            return {"issues": []}
        if "Injection" in system or "SSRF" in system:
            return {"issues": []}
        if "Ops" in system or "CORS" in system:
            return {"issues": []}
        if "Dependency" in system or "CVE" in system:
            return {"issues": []}
        if "裁判" in system:
            return {"verdict": "no_go", "verdict_reasons": ["F1"], "must_fix": ["F1"], "can_ship_later": []}
        # Reporter / aggregator
        return {
            "top_risks": ["[High] Missing ownership check"],
            "detail_sections": {"security": "x", "config": "", "dependency": "", "availability": ""},
            "report_body": "x",
            "checklist": [{"item": "log redaction", "done": None}],
            "finding_notes": {"F1": {"explanation": "e", "fix": "f", "false_positive_hint": ""}},
        }

    monkeypatch.setattr(agents_gonogo, "llm_chat_json", fake_llm_chat_json)
    monkeypatch.setattr(agents_gonogo, "build_context_pack", lambda *a, **k: "ctx")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_PASSES", "1")
    monkeypatch.delenv("PRELAUNCH_ENABLE_VALIDATOR", raising=False)

    findings = [
        NormalizedFinding(id="F1", severity="High", category="sast", title="x", file="a.py", line=1, snippet="x")
    ]
    profile = ProjectProfile(root=tmp_path, has_python=True)
    llm, stages = agents_gonogo.run_multi_agent_pipeline(findings, profile, tmp_path, "dashscope", "k")
    assert llm.verdict in ("go", "no_go", "unknown")
    assert any("ownership" in c.lower() or "AuthZ".lower() in c.lower() for c in calls) or len(calls) >= 5
    assert getattr(stages, "raw", None) is not None


def test_domain_passes_increase_llm_calls(monkeypatch, tmp_path: Path):
    from app.services.prelaunch import agents_gonogo
    from app.services.prelaunch.detect import ProjectProfile

    count = {"n": 0}

    def fake_llm_chat_json(system: str, user: str, llm_provider: str, api_key: str, *, max_tokens: int = 0):
        count["n"] += 1
        if "裁判" in system:
            return {"verdict": "go", "verdict_reasons": ["ok"], "must_fix": [], "can_ship_later": []}
        return {"issues": [], "signals": {}}

    monkeypatch.setattr(agents_gonogo, "llm_chat_json", fake_llm_chat_json)
    monkeypatch.setattr(agents_gonogo, "build_context_pack", lambda *a, **k: "ctx")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_PASSES", "3")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_MIN_VOTES", "2")

    findings = []
    profile = ProjectProfile(root=tmp_path)
    agents_gonogo.run_multi_agent_pipeline(findings, profile, tmp_path, "dashscope", "k")
    # 5 domains * 3 passes + judge + reporter = 17
    assert count["n"] >= 17


def test_validator_can_drop_low_confidence_issues(monkeypatch, tmp_path: Path):
    from app.services.prelaunch import agents_gonogo
    from app.services.prelaunch.detect import ProjectProfile

    def fake_llm_chat_json(system: str, user: str, llm_provider: str, api_key: str, *, max_tokens: int = 0):
        if "AuthZ / Ownership Agent" in system:
            return {"issues": [{"id": "X", "title": "t", "severity": "High", "category": "sast"}], "signals": {}}
        if "Validator" in system:
            return {"keep": False, "reason": "no evidence"}
        if "裁判" in system:
            return {"verdict": "go", "verdict_reasons": ["ok"], "must_fix": [], "can_ship_later": []}
        return {"issues": [], "signals": {}}

    monkeypatch.setattr(agents_gonogo, "llm_chat_json", fake_llm_chat_json)
    monkeypatch.setattr(agents_gonogo, "build_context_pack", lambda *a, **k: "ctx")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_PASSES", "1")
    monkeypatch.setenv("PRELAUNCH_ENABLE_VALIDATOR", "1")
    monkeypatch.setenv("PRELAUNCH_VALIDATOR_MAX_ITEMS", "10")

    findings = []
    profile = ProjectProfile(root=tmp_path)
    llm, stages = agents_gonogo.run_multi_agent_pipeline(findings, profile, tmp_path, "dashscope", "k")
    assert not stages.analyzer.issues


def test_vote_merge_can_cluster_similar_issues_without_same_id(monkeypatch, tmp_path: Path):
    """
    如果不同 pass 给了不同 id，但落在同一 file/line 且 category 相同、标题相近，
    应通过聚类投票保留（避免“必须同 id 才算票”导致漏报）。
    """
    from app.services.prelaunch import agents_gonogo
    from app.services.prelaunch.detect import ProjectProfile

    monkeypatch.setattr(agents_gonogo, "build_context_pack", lambda *a, **k: "ctx")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_PASSES", "2")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_MIN_VOTES", "2")
    monkeypatch.setenv("PRELAUNCH_VOTE_LINE_WINDOW", "3")

    state = {"pass": 0}

    def fake_llm_chat_json(system: str, user: str, llm_provider: str, api_key: str, *, max_tokens: int = 0):
        if "AuthZ / Ownership Agent" in system:
            state["pass"] += 1
            if state["pass"] == 1:
                return {
                    "issues": [
                        {"id": "A1", "title": "Missing ownership check on GET /user/{id}", "severity": "High", "category": "sast", "file": "api.py", "line": 10}
                    ],
                    "signals": {},
                }
            return {
                "issues": [
                    {"id": "B2", "title": "Ownership authorization missing for user profile endpoint", "severity": "High", "category": "sast", "file": "api.py", "line": 11}
                ],
                "signals": {},
            }
        if "裁判" in system:
            return {"verdict": "go", "verdict_reasons": ["ok"], "must_fix": [], "can_ship_later": []}
        return {"issues": [], "signals": {}}

    monkeypatch.setattr(agents_gonogo, "llm_chat_json", fake_llm_chat_json)

    profile = ProjectProfile(root=tmp_path)
    llm, stages = agents_gonogo.run_multi_agent_pipeline([], profile, tmp_path, "dashscope", "k")
    # 期待聚类后仍能保留至少 1 条 issue
    assert len(stages.analyzer.issues) >= 1


def test_vote_merge_does_not_merge_different_rules_same_location(monkeypatch, tmp_path: Path):
    """
    同文件/行号接近/同 category，但 rule 不同（例如不同 semgrep rule、不同 heuristic），不应被误合并为一条。
    """
    from app.services.prelaunch import agents_gonogo
    from app.services.prelaunch.detect import ProjectProfile

    monkeypatch.setattr(agents_gonogo, "build_context_pack", lambda *a, **k: "ctx")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_PASSES", "2")
    monkeypatch.setenv("PRELAUNCH_DOMAIN_MIN_VOTES", "2")
    monkeypatch.setenv("PRELAUNCH_VOTE_LINE_WINDOW", "3")

    def fake_llm_chat_json(system: str, user: str, llm_provider: str, api_key: str, *, max_tokens: int = 0):
        if "Ops / Error & Config Agent" in system:
            # Each pass returns both issues, but they are different rules.
            return {
                "issues": [
                    {
                        "id": "R1",
                        "title": "CORS wildcard",
                        "severity": "High",
                        "category": "config",
                        "file": "server.py",
                        "line": 20,
                        "rule_id": "heuristic:cors_star",
                    },
                    {
                        "id": "R2",
                        "title": "Debug enabled",
                        "severity": "High",
                        "category": "config",
                        "file": "server.py",
                        "line": 19,
                        "rule_id": "heuristic:debug_true",
                    },
                ],
                "signals": {},
            }
        if "裁判" in system:
            return {"verdict": "go", "verdict_reasons": ["ok"], "must_fix": [], "can_ship_later": []}
        return {"issues": [], "signals": {}}

    monkeypatch.setattr(agents_gonogo, "llm_chat_json", fake_llm_chat_json)

    profile = ProjectProfile(root=tmp_path)
    _, stages = agents_gonogo.run_multi_agent_pipeline([], profile, tmp_path, "dashscope", "k")
    # 期待两条都保留，不因 file/line/category 相近而合并
    assert len(stages.analyzer.issues) >= 2

