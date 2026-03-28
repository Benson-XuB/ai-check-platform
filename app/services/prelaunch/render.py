"""Jinja2 HTML 报告（MVP 四区块 + 按安全/配置/依赖/可用性分栏）。"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.prelaunch.schemas import LlmReport, NormalizedFinding, PrelaunchJobRecord


def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "templates"


def bucket_mvp_findings(findings: List[NormalizedFinding]) -> Tuple[Dict[str, List[dict]], Dict[str, int]]:
    keys = ("security", "config", "dependency", "availability", "other")
    buckets: Dict[str, List[dict]] = {k: [] for k in keys}
    for f in findings:
        d = f.model_dump()
        c = f.category
        if c in ("secret", "sast"):
            buckets["security"].append(d)
        elif c == "config":
            buckets["config"].append(d)
        elif c == "dependency":
            buckets["dependency"].append(d)
        elif c == "availability":
            buckets["availability"].append(d)
        else:
            buckets["other"].append(d)
    counts = {k: len(buckets[k]) for k in keys}
    return buckets, counts


def render_html_report(
    record: PrelaunchJobRecord,
    findings: List[NormalizedFinding],
    llm: LlmReport,
    profile_hints: Dict[str, Any],
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_templates_dir())),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("prelaunch_report.html")
    mvp_buckets, mvp_counts = bucket_mvp_findings(findings)
    all_rows = [f.model_dump() for f in findings]
    return tpl.render(
        record=record.model_dump(),
        findings=all_rows,
        findings_mvp=mvp_buckets,
        mvp_counts=mvp_counts,
        llm=llm.model_dump(),
        profile_hints=profile_hints,
        findings_json=json.dumps(all_rows, ensure_ascii=False),
    )
