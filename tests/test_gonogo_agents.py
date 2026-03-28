"""三阶段 Go/No-Go 合并与解析（无网络）。"""

from app.services.prelaunch.agents_gonogo import (
    AnalyzerIssue,
    AnalyzerOutput,
    JudgeOutput,
    ReporterOutput,
    _parse_analyzer,
    _parse_judge,
    gonogo_bundle_to_llm_report,
)


def test_gonogo_bundle_to_llm_report():
    a = AnalyzerOutput(issues=[AnalyzerIssue(id="x1", title="硬编码密钥", severity="Critical")])
    j = JudgeOutput(
        verdict="no_go",
        verdict_reasons=["存在高危项"],
        must_fix=["x1"],
        can_ship_later=["文档"],
    )
    r = ReporterOutput(top_risks=["密钥暴露"], report_body="详情", checklist=[{"item": "轮换密钥", "done": None}])
    llm = gonogo_bundle_to_llm_report(a, j, r)
    assert llm.verdict == "no_go"
    assert "No-Go" in llm.verdict_display or "不建议" in llm.verdict_display
    assert llm.top_risks[0] == "密钥暴露"
    assert llm.must_fix_before_launch == ["x1"]


def test_parse_judge_aliases():
    j = _parse_judge({"verdict": "no-go", "reasons": ["a"], "must_fix": [], "can_ship_later": []})
    assert j.verdict == "no_go"


def test_parse_analyzer_tolerates_bad_rows():
    d = {"issues": [{"id": "1", "title": "ok"}, "not-a-dict", {"title": "t2"}]}
    out = _parse_analyzer(d)
    assert len(out.issues) == 2
