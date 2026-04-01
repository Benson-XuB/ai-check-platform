"""Multi-domain Agents → Judge → Reporter：多领域 LLM 分工，整合为 Go/No-Go 报告。"""

import json
import os
import random
import re
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
    rule_id: str = ""  # optional: semgrep check_id / heuristic rule / cve id


class AnalyzerOutput(BaseModel):
    issues: List[AnalyzerIssue] = Field(default_factory=list)


class DomainOutput(BaseModel):
    """单领域 agent 的输出：issues + 可选结构化信号。"""

    issues: List[AnalyzerIssue] = Field(default_factory=list)
    signals: Dict[str, Any] = Field(default_factory=dict)


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


SYSTEM_ANALYZER = """你是「分析员」：专门审查“AI 生成代码常见坑”的应用安全与上线风险，结合自动化扫描结果与仓库关键文件节选，列出需要关注的问题。

核心要求（必须遵守）：
1) **证据锚点**：每条 issue 必须给出可核查的 evidence，并尽量带 file + line + snippet（若输入已有，沿用；若只能推断，source=heuristic 且明确“需要人工确认”）。
2) **以 AI 常见漏洞为导向**：优先关注以下方向（至少覆盖你看到的高风险点）：
   - 鉴权/越权/ownership：路由/API 是否校验当前用户对资源的归属；是否“按 user_id 参数直接查库返回”
   - Response 敏感字段泄露：password/hash/token/secret/key/internal id、权限字段、第三方凭证
   - 输入校验不足：SQL/NoSQL 注入、命令执行、SSRF、路径穿越、模板注入；尤其是把用户输入拼进 DB/命令/URL/文件路径
   - 错误处理泄露：把 stack trace/SQL/路径/配置回显给客户端；debug 模式、过宽 CORS
   - 密钥与加密：硬编码密钥、弱随机数、不安全 hash/加密模式
   - 依赖与供应链：高危依赖洞的可利用性/修复优先级（与 pip-audit/npm/trivy findings 联动）
   - 不安全文件操作：上传/解压/临时目录/权限/覆盖写；zip slip 等
   - 并发/状态：幂等缺失、竞态、重复扣费/重复写入
   - 可用性与运维：超时/重试/限流、日志脱敏、备份、健康检查
3) **沿用 finding.id**：若 issue 对应已有 finding，id 必须与 finding.id 一致；否则可生成新 id（如 HEUR-1）。
4) **禁止决策**：禁止输出 go/no-go、禁止「可以上线」「建议立即发布」等结论性语句（裁判会做）。

输出：只输出一个 JSON 对象（不要 markdown 围栏），格式：
{"issues": [{"id": "字符串", "title": "短标题", "severity": "Critical|High|Medium|Low|Info", "category": "secret|dependency|sast|config|availability|other", "evidence": "为何是问题（含证据锚点/如何复现/为何属于 AI 常见坑）", "file": "", "line": 0, "source": "tool|heuristic"}]}"""


SYSTEM_AUTHZ = """你是专职「AuthZ / Ownership Agent」：只审查鉴权、越权与资源归属（ownership）问题，尤其是 AI 代码常见坑：按 user_id/path 参数直接查库返回、缺少当前用户与资源的绑定校验。

强约束（必须遵守）：
- 如果 issue 对应输入 findings（你能从输入 JSON 的 raw_refs / sources / id 识别出来），**必须**尽量输出稳定的 rule 标识到 `rule_id` 字段：
  - semgrep: `check_id`
  - bandit: `test_id`
  - gitleaks: `rule`
  - pip-audit/trivy: `CVE id` / `vulnerability id`
  - heuristic: `heuristic:<name>`
- 只有在完全无法推断时，`rule_id` 才可以留空。

必须输出 JSON（不要 markdown 围栏）：
{
  "issues": [
    {"id": "...", "rule_id": "...", "title": "...", "severity": "Critical|High|Medium|Low|Info", "category": "sast", "evidence": "...(含 file/line/snippet 或说明需人工确认)", "file": "", "line": 0, "source": "tool|heuristic"}
  ],
  "signals": {
    "suspected_endpoints": ["可疑路由/handler 标识（若能从上下文识别）"],
    "suspected_resources": ["资源类型（如 order/user/file）"],
    "missing_checks": ["缺失的校验点（如 ownership check / RBAC / tenant check）"]
  }
}
要求：没有锚点不要下结论，写“疑似/需人工确认”。"""


SYSTEM_SENSITIVE = """你是专职「Sensitive Response Agent」：只审查 response/日志/错误中可能泄露敏感字段的问题（password/hash/token/secret/key/internal_id/权限字段/第三方凭证）。

输出 JSON：
{
  "issues": [
    {"id": "...", "rule_id": "...", "title": "...", "severity": "Critical|High|Medium|Low|Info", "category": "sast|secret|config|other", "evidence": "...", "file": "", "line": 0, "source": "tool|heuristic"}
  ],
  "signals": {
    "suspected_fields": ["password","token","secret","key","internal_id","role","permission"],
    "suspected_endpoints": ["..."],
    "leak_vectors": ["response json", "error message", "log", "debug endpoint"]
  }
}
强约束：若 issue 来自输入 findings，请尽量把对应规则标识写入 `rule_id`（如 semgrep check_id / gitleaks rule 等）。无法推断才留空。
要求：必须给 evidence 锚点或标注需人工确认。"""


SYSTEM_INJECTION = """你是专职「Input Validation / Injection Agent」：只审查输入校验不足导致的注入与攻击面（SQL/NoSQL/命令执行/SSRF/路径穿越/模板注入）。

输出 JSON：
{
  "issues": [
    {"id": "...", "rule_id": "...", "title": "...", "severity": "Critical|High|Medium|Low|Info", "category": "sast|config|other", "evidence": "...", "file": "", "line": 0, "source": "tool|heuristic"}
  ],
  "signals": {
    "sources": ["用户输入来源：query/body/path/header/file"],
    "sinks": ["危险 sink：SQL/ORM raw, shell, http client, filesystem path, template render"],
    "missing_validation": ["缺失的校验/编码/参数化/allowlist 点"]
  }
}
强约束：若能把 issue 对齐到输入 findings，请把对应规则标识写入 `rule_id`（semgrep check_id / bandit test_id 等）。无法推断才留空。
要求：优先指出“用户输入如何到达 sink”的证据链；无锚点写需人工确认。"""


SYSTEM_OPS = """你是专职「Ops / Error & Config Agent」：只审查错误处理与配置风险（stack trace/SQL/路径/配置回显，debug，CORS 过宽，超时/重试/限流缺失，日志脱敏等）。

输出 JSON：
{
  "issues": [
    {"id": "...", "rule_id": "...", "title": "...", "severity": "Critical|High|Medium|Low|Info", "category": "config|availability|other", "evidence": "...", "file": "", "line": 0, "source": "tool|heuristic"}
  ],
  "signals": {
    "misconfigs": ["debug=true", "CORS *", "expose .env", "missing timeout"],
    "error_leaks": ["stack trace returned", "sql in error"],
    "operability_gaps": ["no timeout", "no retry", "no rate limit"]
  }
}
强约束：对配置/启发式类问题，请尽量给出稳定 `rule_id`（如 heuristic:cors_star / heuristic:debug_true），以便投票聚类更精确。
要求：无锚点不要断言。"""


SYSTEM_DEPENDENCY = """你是专职「Dependency / Supply-chain Agent」：只审查依赖与供应链风险。结合 pip-audit/npm-audit/trivy findings，输出最小可执行的升级/替代方案，并按“可利用性 × 修复成本”排序。

输出 JSON：
{
  "issues": [
    {"id": "...", "rule_id": "...", "title": "...", "severity": "Critical|High|Medium|Low|Info", "category": "dependency", "evidence": "...", "file": "", "line": 0, "source": "tool|heuristic"}
  ],
  "signals": {
    "top_actions": ["先升级什么到什么版本（如可推断）/先移除或缓解什么"],
    "breaking_risks": ["可能的 breaking change 风险点"],
    "runtime_exposure": ["是否对公网暴露/是否仅 dev 依赖（不确定请写需确认）"]
  }
}
强约束：依赖类 issue 的 `rule_id` 优先填漏洞标识（CVE/OSV/ghsa 等）或工具规则 id；能从输入 raw_refs 推断就必须填。
要求：不要罗列无穷 CVE；输出 Top 3~8 个可执行动作。"""


SYSTEM_VALIDATOR = """你是「Validator」：严苛复核器。你会收到一条候选 issue（含 evidence/file/line/snippet）与仓库上下文节选。
你的任务是判断该 issue 是否“可信且可操作”。如果它是臆测、无证据、或无法从上下文核查，应当丢弃。

只输出 JSON（不要 markdown 围栏）：
{"keep": true|false, "reason": "一句话原因（可选）"}

规则：
- 若 issue.source=heuristic 且 evidence 为空或没有可核查锚点，倾向 keep=false
- 若 evidence 明确指出文件/行号/片段或可核查配置项，倾向 keep=true
- 不要编造证据。"""


SYSTEM_JUDGE = """你是「裁判 / CTO」：只做上线前 Go / No-Go 决策。输入仅为分析员 issues 列表（JSON），你看不到源代码。
原则（非法律结论）：存在 Critical 密钥/凭证暴露、明确高危未鉴权/越权（ownership 缺失）、敏感字段泄露到 response、可远程利用的 RCE/SSRF/注入、生产环境 debug/CORS * 等 → 倾向 no_go。
只输出 JSON：
{"verdict": "go" 或 "no_go",
 "verdict_reasons": ["1～3 条：No-Go 或「待定」时写阻断原因；若 verdict 为 go 可写 1 句总结当前风险姿态"],
 "optimize_suggestions": ["verdict 为 go 时填 1～4 条可优化项；No-Go 时可为空"],
 "must_fix": ["上线前必须处理，对应 issue id 或短描述"],
 "can_ship_later": ["可上线后排期"]}"""


SYSTEM_REPORTER = """你是「报告撰写人」：把分析员 issues 与裁判结论整理成上线前报告（中文 Markdown 片段），写作目标是让“小团队在 30~60 分钟内按优先级修完 AI 常见坑”。

写作要求（必须遵守）：
1) 每个高风险点必须包含：问题是什么 / 影响是什么 / 最小修复动作（给出可直接套用的修复 pattern）/ 如何验证修复
2) 对以下 AI 常见坑，若 issues 中出现相关内容，必须在 detail_sections 里给出明确小节与修复建议：
   - 鉴权/越权/ownership
   - Response 敏感字段泄露
   - 输入校验不足（注入/SSRF/路径穿越/命令执行）
   - 错误处理泄露与 debug/CORS
   - 依赖高危修复优先级（按“可利用性×修复成本”排序）
   - 不安全文件操作（上传/解压/临时目录）
   - 并发/幂等/竞态
3) 不要声称“已确认存在漏洞”除非 issue evidence 有明确锚点；没有锚点的写“疑似/需人工确认”。
4) `finding_notes` 的引用策略：优先围绕 `rule_id` 组织为“问题模式”来写（同一个 rule_id 可能在多个文件/位置命中）：
   - 如果多个 issue 共享同一 `rule_id`，可以在 `finding_notes` 中只对其中 1～2 个代表性 issue.id 写详解，并在 explanation 中注明“同 rule_id 多处命中”
   - detail_sections 中也应按 rule_id 汇总修复思路（避免逐条重复）
   - 若 issue 缺少 rule_id，则退化按 issue.id 逐条写

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
                rule_id=str(item.get("rule_id") or item.get("rule") or item.get("check_id") or ""),
            )
        )
    return AnalyzerOutput(issues=out)


def _parse_domain(d: Dict[str, Any]) -> DomainOutput:
    if d.get("_parse_error"):
        return DomainOutput()
    base = _parse_analyzer(d)
    sig = d.get("signals") if isinstance(d.get("signals"), dict) else {}
    return DomainOutput(issues=base.issues, signals=sig or {})


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


def _merge_issues(*parts: List[AnalyzerIssue]) -> List[AnalyzerIssue]:
    """按 id 去重合并，保留首个出现的 issue。"""
    out: List[AnalyzerIssue] = []
    seen: set = set()
    for issues in parts:
        for it in issues:
            if it.id in seen:
                continue
            seen.add(it.id)
            out.append(it)
    return out


def _domain_passes() -> int:
    try:
        return max(1, min(6, int(os.getenv("PRELAUNCH_DOMAIN_PASSES", "1"))))
    except ValueError:
        return 1


def _domain_min_votes() -> int:
    try:
        return max(1, min(5, int(os.getenv("PRELAUNCH_DOMAIN_MIN_VOTES", "1"))))
    except ValueError:
        return 1


def _validator_enabled() -> bool:
    return os.getenv("PRELAUNCH_ENABLE_VALIDATOR", "").strip().lower() in ("1", "true", "yes")


def _validator_max_items() -> int:
    try:
        return max(1, min(30, int(os.getenv("PRELAUNCH_VALIDATOR_MAX_ITEMS", "15"))))
    except ValueError:
        return 15


def _vote_line_window() -> int:
    try:
        return max(1, min(10, int(os.getenv("PRELAUNCH_VOTE_LINE_WINDOW", "3"))))
    except ValueError:
        return 3


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _sig_keywords(text: str, max_words: int = 8) -> str:
    t = _norm_text(text)
    # keep alnum + CJK + a few separators
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff\s_./-]+", " ", t)
    words = [w for w in t.split(" ") if w and len(w) > 1]
    return " ".join(words[:max_words])


_CONCEPT_KEYWORDS = (
    # authz / data access
    "ownership",
    "authz",
    "authorization",
    "permission",
    "rbac",
    "abac",
    "tenant",
    # sensitive data
    "token",
    "secret",
    "password",
    "apikey",
    "api_key",
    # injections
    "ssrf",
    "sqli",
    "sql injection",
    "rce",
    "path traversal",
    "zip slip",
    "command injection",
    "template injection",
    # config/ops
    "cors",
    "debug",
    "stack trace",
)


def _concept_sig(issue: AnalyzerIssue) -> str:
    """Coarse concept signature to stabilize clustering across paraphrases."""
    hay = _norm_text(f"{issue.title} {issue.evidence}")
    for kw in _CONCEPT_KEYWORDS:
        k = _norm_text(kw)
        if k and k in hay:
            return k.replace(" ", "_")
    return ""


def _cluster_key(issue: AnalyzerIssue, *, line_window: int) -> Tuple[str, int, str, str]:
    f = (issue.file or "").strip()
    ln = int(issue.line or 0)
    bucket = (ln // max(1, line_window)) if ln > 0 else 0
    cat = _norm_text(issue.category or "")
    rule = _norm_text(issue.rule_id or "")
    if rule:
        return (f, bucket, cat, f"rule:{rule}")
    concept = _concept_sig(issue)
    if concept:
        return (f, bucket, cat, f"concept:{concept}")
    sig = _sig_keywords(issue.title or "", max_words=6)
    return (f, bucket, cat, f"sig:{sig}")


def _vote_merge_issues(issues: List[AnalyzerIssue], *, min_votes: int) -> List[AnalyzerIssue]:
    """
    聚类投票：以 (file, line±window, category, keyword-signature) 聚类计票。
    达到 min_votes 的 cluster 才保留；cluster 内 severity 取最高、line 取最小正数、title/evidence 取代表条目。
    """
    if not issues:
        return []
    line_window = _vote_line_window()
    if min_votes <= 1:
        return _merge_issues(issues)

    severity_order = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}
    clusters: Dict[Tuple[str, int, str, str], List[AnalyzerIssue]] = {}
    for it in issues:
        k = _cluster_key(it, line_window=line_window)
        clusters.setdefault(k, []).append(it)

    kept: List[AnalyzerIssue] = []
    for _, items in clusters.items():
        if len(items) < min_votes:
            continue
        best = max(items, key=lambda x: severity_order.get(x.severity or "Info", 0))
        lines = [int(x.line or 0) for x in items if int(x.line or 0) > 0]
        rep_line = min(lines) if lines else 0
        kept.append(
            AnalyzerIssue(
                id=best.id,
                title=best.title,
                severity=best.severity,
                category=best.category,
                evidence=best.evidence,
                file=best.file,
                line=rep_line,
                source=best.source,
            )
        )

    kept.sort(key=_issue_sort_key)
    return kept


def _run_domain_with_passes(
    system_prompt: str,
    *,
    base_user: str,
    findings_payload: List[Dict[str, Any]],
    llm_provider: str,
    api_key: str,
    max_tokens: int,
    passes: int,
) -> Tuple[DomainOutput, List[Dict[str, Any]]]:
    raws: List[Dict[str, Any]] = []
    all_issues: List[AnalyzerIssue] = []
    merged_signals: Dict[str, Any] = {}
    for i in range(1, passes + 1):
        # 为了“独立 pass”效果，打乱 findings 顺序（确定性 seed）
        rng = random.Random(hash((system_prompt[:20], i)) & 0xFFFFFFFF)
        shuffled = list(findings_payload)
        rng.shuffle(shuffled)
        user = base_user.replace(
            json.dumps(findings_payload, ensure_ascii=False, indent=2),
            json.dumps(shuffled, ensure_ascii=False, indent=2),
        )
        raw = llm_chat_json(system_prompt, user, llm_provider, api_key, max_tokens=max_tokens)
        raws.append(raw)
        out = _parse_domain(raw)
        all_issues.extend(out.issues)
        # signals 合并：同 key 的 list 做并集；其他覆盖为首个非空
        for k, v in (out.signals or {}).items():
            if v is None:
                continue
            if isinstance(v, list):
                cur = merged_signals.get(k)
                if not isinstance(cur, list):
                    cur = []
                merged_signals[k] = list(dict.fromkeys(list(cur) + [str(x) for x in v if x is not None]))
            elif k not in merged_signals and v not in ("", {}, []):
                merged_signals[k] = v
    voted = _vote_merge_issues(all_issues, min_votes=_domain_min_votes())
    return DomainOutput(issues=voted, signals=merged_signals), raws


def _validate_issues(
    issues: List[AnalyzerIssue],
    *,
    ctx: str,
    llm_provider: str,
    api_key: str,
    max_tokens: int = 700,
) -> List[AnalyzerIssue]:
    if not issues:
        return issues
    keep: List[AnalyzerIssue] = []
    for it in issues[: _validator_max_items()]:
        user = json.dumps({"issue": it.model_dump(), "context_pack": ctx[:12000]}, ensure_ascii=False, indent=2)
        raw = llm_chat_json(SYSTEM_VALIDATOR, user, llm_provider, api_key, max_tokens=max_tokens)
        if isinstance(raw, dict) and raw.get("keep") is True:
            keep.append(it)
    # 超过 max_items 的剩余项，保守保留（避免 validator 成本爆炸）
    if len(issues) > _validator_max_items():
        keep.extend(issues[_validator_max_items() :])
    return keep


def run_multi_agent_pipeline(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    repo_root: Path,
    llm_provider: str,
    api_key: str,
) -> Tuple[LlmReport, GonogoStages]:
    """
    多领域 agents（AuthZ / Sensitive / Injection / Ops / Dependency）→ Judge → Reporter。
    输出保持与旧版一致：GonogoStages.analyzer 为“合并后的 issues”。
    """
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
    base_user = (
        f"【仓库探测】\n{json.dumps(prof, ensure_ascii=False, indent=2)}\n\n"
        f"【归一化 findings（截断后）】\n{json.dumps(findings_payload, ensure_ascii=False, indent=2)}\n\n"
        f"【关键文件节选】\n{ctx}"
    )

    max_a = int(os.getenv("PRELAUNCH_ANALYZER_MAX_TOKENS", "6144"))
    max_j = int(os.getenv("PRELAUNCH_JUDGE_MAX_TOKENS", "2048"))
    max_r = int(os.getenv("PRELAUNCH_REPORTER_MAX_TOKENS", "6144"))

    passes = _domain_passes()
    authz, authz_raws = _run_domain_with_passes(
        SYSTEM_AUTHZ,
        base_user=base_user,
        findings_payload=findings_payload,
        llm_provider=llm_provider,
        api_key=api_key,
        max_tokens=max_a,
        passes=passes,
    )
    sensitive, sensitive_raws = _run_domain_with_passes(
        SYSTEM_SENSITIVE,
        base_user=base_user,
        findings_payload=findings_payload,
        llm_provider=llm_provider,
        api_key=api_key,
        max_tokens=max_a,
        passes=passes,
    )
    injection, inj_raws = _run_domain_with_passes(
        SYSTEM_INJECTION,
        base_user=base_user,
        findings_payload=findings_payload,
        llm_provider=llm_provider,
        api_key=api_key,
        max_tokens=max_a,
        passes=passes,
    )
    ops, ops_raws = _run_domain_with_passes(
        SYSTEM_OPS,
        base_user=base_user,
        findings_payload=findings_payload,
        llm_provider=llm_provider,
        api_key=api_key,
        max_tokens=max_a,
        passes=passes,
    )
    dependency, dep_raws = _run_domain_with_passes(
        SYSTEM_DEPENDENCY,
        base_user=base_user,
        findings_payload=findings_payload,
        llm_provider=llm_provider,
        api_key=api_key,
        max_tokens=max_a,
        passes=passes,
    )

    merged_issues = _merge_issues(
        authz.issues, sensitive.issues, injection.issues, ops.issues, dependency.issues
    )
    if _validator_enabled():
        merged_issues = _validate_issues(merged_issues, ctx=ctx, llm_provider=llm_provider, api_key=api_key)
    analyzer = AnalyzerOutput(issues=merged_issues)

    merged_signals = {
        "authz": authz.signals,
        "sensitive": sensitive.signals,
        "injection": injection.signals,
        "ops": ops.signals,
        "dependency": dependency.signals,
    }

    judge_input = json.dumps({"issues": [i.model_dump() for i in analyzer.issues]}, ensure_ascii=False, indent=2)
    j_raw = llm_chat_json(SYSTEM_JUDGE, judge_input, llm_provider, api_key, max_tokens=max_j)
    judge = _parse_judge(j_raw)

    reporter_input = json.dumps(
        {
            "issues": [i.model_dump() for i in analyzer.issues],
            "signals": merged_signals,
            "judge": judge.model_dump(),
        },
        ensure_ascii=False,
        indent=2,
    )
    r_raw = llm_chat_json(SYSTEM_REPORTER, reporter_input, llm_provider, api_key, max_tokens=max_r)
    reporter = _parse_reporter(r_raw)

    llm = gonogo_bundle_to_llm_report(analyzer, judge, reporter)
    raw_debug = {
        "authz": authz_raws,
        "sensitive": sensitive_raws,
        "injection": inj_raws,
        "ops": ops_raws,
        "dependency": dep_raws,
        "judge": j_raw,
        "reporter": r_raw,
    }
    return llm, GonogoStages(analyzer=analyzer, judge=judge, reporter=reporter, raw=raw_debug)
