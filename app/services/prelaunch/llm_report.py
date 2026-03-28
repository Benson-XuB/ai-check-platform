"""Prelaunch LLM：默认三阶段（Analyzer→Judge→Reporter）；可设 PRELAUNCH_LEGACY_LLM=1 回退单轮。"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.services.llm_defaults import get_public_default_llm_provider
from app.services.prelaunch.agents_gonogo import GonogoStages, run_three_agent_pipeline
from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.schemas import LlmReport, NormalizedFinding
from app.services.review import _call_dashscope


def _shallow_tree(root: Path, max_depth: int = 2, max_entries: int = 80) -> str:
    lines: List[str] = []
    count = 0
    root = root.resolve()
    for p in root.rglob("*"):
        if count >= max_entries:
            lines.append("… (truncated)")
            break
        rel = p.relative_to(root)
        if any(x in rel.parts for x in ("node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build")):
            continue
        depth = len(rel.parts)
        if depth > max_depth or not p.is_dir() and depth > max_depth:
            continue
        prefix = "  " * (depth - 1) if depth else ""
        name = rel.name + ("/" if p.is_dir() else "")
        lines.append(f"{prefix}{name}")
        count += 1
    return "\n".join(lines[: max_entries + 1])


def _build_legacy_prompt(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    tree: str,
) -> str:
    payload = [f.model_dump() for f in findings[:200]]
    prof = {
        "has_python": profile.has_python,
        "has_node": profile.has_node,
        "has_java": profile.has_java,
        "has_javascript": profile.has_javascript,
        "has_maven": profile.has_maven,
        "has_gradle": profile.has_gradle,
        "package_managers": profile.package_managers,
        "lockfiles": profile.lockfiles,
    }
    return f"""你是应用安全与架构顾问，面向小团队上线前自查（非正式渗透/合规结论）。

【仓库探测】
{json.dumps(prof, ensure_ascii=False, indent=2)}

【目录节选】
```
{tree[:12000]}
```

【扫描器归一化结果（最多 200 条）】
{json.dumps(payload, ensure_ascii=False, indent=2)}

请只输出一个 JSON 对象，不要 markdown 围栏，键如下：
{{
  "executive_summary": "3-8 句执行摘要",
  "top_risks": ["字符串列表，最多 10 条"],
  "finding_notes": {{
     "<finding.id 必须与输入一致>": {{
        "explanation": "人话说明",
        "fix": "修复建议",
        "false_positive_hint": "可能误报时的判断提示，无则空字符串"
     }}
  }},
  "architecture_section": "轻量架构评审：边界、数据流风险、单点建议；首句注明「启发式，非正式架构评审决议」",
  "compliance_checklist": [
     {{"item": "检查项描述", "done": null}}
  ]
}}
finding_notes 仅覆盖你认为最重要的至多 40 条 finding.id；compliance_checklist 的 done 未知填 null（不要编造已落实）。
"""


def _parse_llm_json(text: str) -> LlmReport:
    text = text.strip()
    for start in ("{", "```json", "```"):
        i = text.find(start)
        if i >= 0:
            chunk = text[i:].replace("```json", "").replace("```", "").strip()
            try:
                obj = json.loads(chunk)
                return LlmReport.model_validate(obj)
            except Exception:
                continue
    return LlmReport(executive_summary=text[:2000], architecture_section="（LLM 返回非 JSON，已原文截断保存）")


def _generate_llm_report_legacy(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    repo_root: Path,
    llm_provider: str,
    api_key: str,
) -> LlmReport:
    tree = _shallow_tree(repo_root)
    prompt = _build_legacy_prompt(findings, profile, tree)
    content = ""
    provider = (llm_provider or get_public_default_llm_provider()).lower().strip()
    try:
        if provider == "dashscope":
            content = _call_dashscope(api_key, "qwen-plus", prompt, max_tokens=8000, temperature=0.2)
        else:
            with httpx.Client(timeout=180) as client:
                r = client.post(
                    "https://api.moonshot.cn/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "moonshot-v1-32k",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                    },
                )
            if r.status_code != 200:
                raise RuntimeError(r.text[:400])
            content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        return LlmReport(
            executive_summary=f"LLM 调用失败: {e}",
            architecture_section="",
        )
    report = _parse_llm_json(content)
    if not report.executive_summary and content:
        report = LlmReport(executive_summary=content[:3000])
    return report


def _gonogo_stages_blob(stages: GonogoStages) -> Dict[str, Any]:
    return {
        "analyzer": {"issues": [i.model_dump() for i in stages.analyzer.issues]},
        "judge": stages.judge.model_dump(),
        "reporter": stages.reporter.model_dump(),
        "raw_model_outputs": stages.raw,
    }


def generate_llm_report_with_stages(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    repo_root: Path,
    llm_provider: str,
    api_key: str,
) -> Tuple[LlmReport, Optional[Dict[str, Any]]]:
    legacy = os.getenv("PRELAUNCH_LEGACY_LLM", "").lower() in ("1", "true", "yes")
    if legacy:
        return _generate_llm_report_legacy(findings, profile, repo_root, llm_provider, api_key), None
    try:
        llm, stages = run_three_agent_pipeline(findings, profile, repo_root, llm_provider, api_key)
        return llm, _gonogo_stages_blob(stages)
    except Exception as e:
        llm_fb = _generate_llm_report_legacy(findings, profile, repo_root, llm_provider, api_key)
        note = f"[三阶段流水线异常，已回退单轮] {e}\n\n"
        llm_fb.executive_summary = note + (llm_fb.executive_summary or "")
        return llm_fb, {"pipeline_error": str(e), "fallback": "legacy_single_shot"}


def generate_llm_report(
    findings: List[NormalizedFinding],
    profile: ProjectProfile,
    repo_root: Path,
    llm_provider: str,
    api_key: str,
) -> LlmReport:
    r, _ = generate_llm_report_with_stages(findings, profile, repo_root, llm_provider, api_key)
    return r
