"""压缩传给 LLM 的 findings：依赖类仅保留高危 + 少量中危，降低噪声与费用。"""

import os
from typing import List

from app.services.prelaunch.schemas import NormalizedFinding


def _sev_rank(s: str) -> int:
    return {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}.get(s, 0)


def cap_findings_for_llm(findings: List[NormalizedFinding]) -> List[NormalizedFinding]:
    """全量 findings 仍用于 HTML；此子集用于 Analyzer 输入。"""
    try:
        med_cap = max(5, int(os.getenv("PRELAUNCH_LLM_DEP_MEDIUM_CAP", "20")))
    except ValueError:
        med_cap = 20
    dep_high: List[NormalizedFinding] = []
    dep_med: List[NormalizedFinding] = []
    rest: List[NormalizedFinding] = []
    for f in findings:
        if f.category == "dependency":
            r = _sev_rank(f.severity)
            if r >= 3:
                dep_high.append(f)
            elif r == 2:
                dep_med.append(f)
            else:
                continue  # Low/Info 依赖不送 LLM
        else:
            rest.append(f)
    dep_med.sort(key=lambda x: _sev_rank(x.severity), reverse=True)
    return rest + dep_high + dep_med[:med_cap]
