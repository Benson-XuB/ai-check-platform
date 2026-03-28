"""Analyzer → Judge → Reporter：线性三角色 LLM，同一模型不同 system prompt。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from app.services.prelaunch.context_pack import build_context_pack
from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.findings_cap import cap_findings_for_llm
from app.services.prelaunch.llm_client import llm_chat_json
from app.services.prelaunch.schemas import LlmReport, NormalizedFinding


class AnalyzerIssue(BaseModel):
    id: str
    title: str
    severity: str = "Medium"
    category: str = "other"
    evidence: str = ""
    file: str = ""
    line: int = 0
    source: str = "heuristic"  # tool | heuristic


class AnalyzerOutput(BaseModel):
    issues: List[AnalyzerIssue] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    verdict: str = "unknown"
    verdict_reasons: List[str] = Field(default_factory=list)  # No-Go/待定：主要原因 1～3 条
    optimize_suggestions: List[str] = Field(default_factory=list)  # Go：建议优化（非阻断）
    must_fix: List[str] = Field(default_factory=list)
    can_ship_later: List[str] = Field(default_factory=list)


class ReporterOutput(BaseModel):
    top_risks: List[str] = Field(default_factory=list)
    report_body: str = ""
    checklist: List[Dict[str, Any]] = Field(default_factory=list)
    finding_notes: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    detail_security: str = ""
    detail_config: str = ""
    detail_dependency: str = ""
    detail_availability: str = ""


SYSTEM_ANALYZER = """你是「分析员」：结合自动化扫描结果与仓库关键文件节选，列出需要关注的安全与配置问题。
职责：解释证据、归类、标严重度；优先沿用输入中的 finding.id；可参考每条中的 mvp_bucket（blocking/later/info）。
禁止：输出 go/no-go、禁止「可以上线」「建议立即发布」等决策性语句。

只输出一个 JSON 对象（不要 markdown 围栏），格式：
{"issues": [{"id": "字符串", "title": "短标题", "severity": "Critical|High|Medium|Low|Info", "category": "secret|dependency|sast|config|availability|other", "evidence": "为何是问题", "file": "", "line": 0, "source": "tool|heuristic"}]}"""


SYSTEM_JUDGE = """你是「裁判 / CTO」：只做上线前 Go / No-Go 决策。输入仅为分析员 issues 列表（JSON），你看不到源代码。
原则（非法律结论）：存在 Critical 密钥/凭证暴露、明确高危未鉴权、可远程利用的 RCE 级依赖洞、生产环境 debug/CORS * 等 → 倾向 no_go。
只输出 JSON：
{"verdict": "go" 或 "no_go",
 "verdict_reasons": ["1～3 条：No-Go 或「待定」时写阻断原因；若 verdict 为 go 可写 1 句总结当前风险姿态"],
 "optimize_suggestions": ["verdict 为 go 时填 1～4 条可优化项；No-Go 时可为空"],
 "must_fix": ["上线前必须处理，对应 issue id 或短描述"],
 "can_ship_later": ["可上线后排期"]}"""


SYSTEM_REPORTER = """你是「报告撰写人」：把分析员 issues 与裁判结论整理成上线前报告（中文 Markdown 片段）。
只输出 JSON（不要 markdown 围栏）：
{"top_risks": ["最多 3 条，按严重度与影响排序：安全>配置>依赖>可用性"],
 "detail_sections": {
   "security": "安全模块：每条含 问题是什么 / 为何严重 / 怎么修（可含简短代码或配置示例）",
   "config": "配置与环境模块，同上",
   "dependency": "依赖与 CVE 模块，同上",
   "availability": "可用性与错误处理模块，同上"
 },
 "report_body": "可选总述；若无则可与 detail 合并的摘要",
 "checklist": [{"item": "发布前易遗漏项（如隐私政策页、Sentry、日志脱敏）", "done": null}],
 "finding_notes": {"<issue id>": {"explanation": "人话", "fix": "建议", "false_positive_hint": ""}}}
finding_notes 至多 30 条；detail_sections 各字符串可为多段，用 \\n\\n 分段。"""


def _norm_verdict(raw: str) -> str:
    s = (raw or "").lower().strip().replace("-", "_")
    if s in ("go", "ship", "yes", "pass", "ok"):
        return "go"
    if s in ("no_go", "nogo", "hold", "stop", "fail", "block"):
        return "no_go"
    return "unknown"


def _parse_analyzer(d: Dict[str, Any]) -> AnalyzerOutput:
    if d.get("_parse_error"):
        return AnalyzerOutput()
    out: List[AnalyzerIssue] = []
    for i, item in enumerate(d.get("issues") or []):
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or f"ANAL-{i + 1}")
        out.append(
            AnalyzerIssue(
                id=oid,
                title=str(item.get("title") or "未命名问题"),
                severity=str(item.get("severity") or "Medium"),
                category=str(item.get("category") or "other"),
                evidence=str(item.get("evidence") or ""),
                file=str(item.get("file") or ""),
                line=int(item.get("line") or 0),
                source=str(item.get("source") or "heuristic"),
            )
        )
    return AnalyzerOutput(issues=out)


def _parse_judge(d: Dict[str, Any]) -> JudgeOutput:
    if d.get("_parse_error"):
        return JudgeOutput(
            verdict="unknown",
            verdict_reasons=["裁判阶段 JSON 解析失败，请查看原始 LLM 输出。"],
        )
    v = _norm_verdict(str(d.get("verdict", "")))
    vr = d.get("verdict_reasons") or d.get("reasons") or []
    opt = d.get("optimize_suggestions") or []
    return JudgeOutput(
        verdict=v,
        verdict_reasons=[str(x) for x in vr if x is not None][:8],
        optimize_suggestions=[str(x) for x in opt if x is not None][:8],
        must_fix=[str(x) for x in (d.get("must_fix") or []) if x is not None][:30],
        can_ship_later=[str(x) for x in (d.get("can_ship_later") or []) if x is not None][:30],
    )


def _parse_reporter(d: Dict[str, Any]) -> ReporterOutput:
    if d.get("_parse_error"):
        return ReporterOutput(report_body="报告阶段 JSON 解析失败。")
    notes: Dict[str, Dict[str, str]] = {}
    raw_notes = d.get("finding_notes") or {}
    if isinstance(raw_notes, dict):
        for k, v in list(raw_notes.items())[:30]:
            if not isinstance(v, dict):
                continue
            notes[str(k)] = {
                "explanation": str(v.get("explanation", "")),
                "fix": str(v.get("fix", "")),
                "false_positive_hint": str(v.get("false_positive_hint", "")),
            }
    chk = d.get("checklist") or []
    checklist: List[Dict[str, Any]] = []
    if isinstance(chk, list):
        for row in chk:
            if isinstance(row, dict) and row.get("item"):
                checklist.append({"item": str(row["item"]), "done": row.get("done")})
    top = [str(x) for x in (d.get("top_risks") or []) if x][:3]
    ds = d.get("detail_sections") if isinstance(d.get("detail_sections"), dict) else {}
    return ReporterOutput(
        top_risks=top,
        report_body=str(d.get("report_body") or ""),
        checklist=checklist,
        finding_notes=notes,
        detail_security=str(ds.get("security") or ""),
        detail_config=str(ds.get("config") or ""),
        detail_dependency=str(ds.get("dependency") or ""),
        detail_availability=str(ds.get("availability") or ""),
    )


def _issue_sort_key(issue: AnalyzerIssue) -> tuple:
    cat = (issue.category or "other").lower()
    group = {"secret": 0, "sast": 0, "config": 1, "dependency": 2, "availability": 3}.get(cat, 4)
    sev = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}.get(issue.severity, 0)
    return (group, -sev)


def _merge_top_risks(
    reporter_top: List[str],
    analyzer_issues: List[AnalyzerIssue],
    verdict_reasons: List[str],
    optimize_suggestions: List[str],
) -> List[str]:
    sorted_issues = sorted(analyzer_issues, key=_issue_sort_key)
    out: List[str] = []
    for t in reporter_top:
        if t and t not in out:
            out.append(t)
        if len(out) >= 3:
            return out
    for i in sorted_issues:
        label = f"[{i.severity}] {i.title}"
        if label not in out:
            out.append(label)
        if len(out) >= 3:
            return out
    for r in verdict_reasons + optimize_suggestions:
        if r and r not in out:
            out.append(r)
        if len(out) >= 3:
            break
    return out[:3]


def gonogo_bundle_to_llm_report(
    analyzer: AnalyzerOutput,
    judge: JudgeOutput,
    reporter: ReporterOutput,
) -> LlmReport:
    v = judge.verdict if judge.verdict in ("go", "no_go") else "unknown"
    if v == "go":
        disp = "可以上线（Go）"
    elif v == "no_go":
        disp = "不建议上线（No-Go）"
    else:
        disp = "结论待定"

    top = _merge_top_risks(
        reporter.top_risks,
        analyzer.issues,
        judge.verdict_reasons,
        judge.optimize_suggestions,
    )

    reasons_block = "\n".join(f"- {r}" for r in judge.verdict_reasons) if judge.verdict_reasons else "（无）"
    opt_block = "\n".join(f"- {r}" for r in judge.optimize_suggestions) if judge.optimize_suggestions else ""
    exec_summary = f"{disp}。\n\n"
    if v == "go" and opt_block:
        exec_summary += f"建议优化：\n{opt_block}\n\n"
    exec_summary += f"裁判要点：\n{reasons_block}"
    if judge.must_fix:
        exec_summary += "\n\n上线前必须处理：\n" + "\n".join(f"- {m}" for m in judge.must_fix[:15])

    arch = reporter.report_body.strip()
    if not arch:
        arch = "\n\n".join(
            x
            for x in (
                reporter.detail_security,
                reporter.detail_config,
                reporter.detail_dependency,
                reporter.detail_availability,
            )
            if x.strip()
        )
    if not arch:
        arch = "（报告正文为空）"

    return LlmReport(
        executive_summary=exec_summary,
        top_risks=top,
        finding_notes=reporter.finding_notes,
        architecture_section=arch,
        compliance_checklist=reporter.checklist,
        verdict=v,
        verdict_display=disp,
        verdict_reasons=judge.verdict_reasons[:3],
        optimize_suggestions=judge.optimize_suggestions[:4],
        must_fix_before_launch=judge.must_fix,
        fix_after_launch=judge.can_ship_later,
        detail_security=reporter.detail_security,
        detail_config=reporter.detail_config,
        detail_dependency=reporter.detail_dependency,
        detail_availability=reporter.detail_availability,
    )


@dataclass
class GonogoStages:
    analyzer: AnalyzerOutput
    judge: JudgeOutput
    reporter: ReporterOutput
    raw: Dict[str, Any]


def run_three_agent_pipeline(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    repo_root: Path,
    llm_provider: str,
    api_key: str,
) -> Tuple[LlmReport, GonogoStages]:
    capped = cap_findings_for_llm(findings)
    findings_payload = [f.model_dump() for f in capped[:200]]
    prof = {
        "has_python": profile.has_python,
        "has_node": profile.has_node,
        "has_java": profile.has_java,
        "package_managers": profile.package_managers,
        "lockfiles": profile.lockfiles,
    }
    ctx = build_context_pack(repo_root, profile)
    user_analyzer = (
        f"【仓库探测】\n{json.dumps(prof, ensure_ascii=False, indent=2)}\n\n"
        f"【说明】依赖类发现已在服务端按「高危优先」截断后送入本轮；完整依赖列表以 HTML 报告为准。\n"
        f"【归一化 findings（截断后）】\n{json.dumps(findings_payload, ensure_ascii=False, indent=2)}\n\n"
        f"【关键文件节选】\n{ctx}"
    )
    max_a = int(os.getenv("PRELAUNCH_ANALYZER_MAX_TOKENS", "6144"))
    max_j = int(os.getenv("PRELAUNCH_JUDGE_MAX_TOKENS", "2048"))
    max_r = int(os.getenv("PRELAUNCH_REPORTER_MAX_TOKENS", "6144"))

    a_raw = llm_chat_json(SYSTEM_ANALYZER, user_analyzer, llm_provider, api_key, max_tokens=max_a)
    analyzer = _parse_analyzer(a_raw)

    judge_input = json.dumps({"issues": [i.model_dump() for i in analyzer.issues]}, ensure_ascii=False, indent=2)
    j_raw = llm_chat_json(SYSTEM_JUDGE, judge_input, llm_provider, api_key, max_tokens=max_j)
    judge = _parse_judge(j_raw)

    reporter_input = json.dumps(
        {"issues": [i.model_dump() for i in analyzer.issues], "judge": judge.model_dump()},
        ensure_ascii=False,
        indent=2,
    )
    r_raw = llm_chat_json(SYSTEM_REPORTER, reporter_input, llm_provider, api_key, max_tokens=max_r)
    reporter = _parse_reporter(r_raw)

    llm = gonogo_bundle_to_llm_report(analyzer, judge, reporter)
    raw_debug = {
        "analyzer": a_raw,
        "judge": j_raw,
        "reporter": r_raw,
    }
    return llm, GonogoStages(analyzer=analyzer, judge=judge, reporter=reporter, raw=raw_debug)
