"""用全量 findings 补全 LLM 报告中的非关键项等（不调用模型）。"""

from typing import List

from app.services.prelaunch.schemas import LlmReport, NormalizedFinding


def enrich_llm_from_findings(llm: LlmReport, findings: List[NormalizedFinding]) -> None:
    llm.non_critical_notes = [
        f"{f.title}（{f.severity}，{f.file or '—'}）"
        for f in findings
        if f.mvp_bucket == "info"
    ][:30]
