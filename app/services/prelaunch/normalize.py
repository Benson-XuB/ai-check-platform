"""去重与合并同源 findings。"""

from typing import Dict, List

from app.services.prelaunch.mvp_buckets import merge_mvp_bucket
from app.services.prelaunch.schemas import NormalizedFinding


def dedupe_findings(findings: List[NormalizedFinding]) -> List[NormalizedFinding]:
    """按 (file, line, category, title) 合并，sources 累加。"""
    key_map: Dict[str, NormalizedFinding] = {}
    for f in findings:
        key = f"{f.file}|{f.line}|{f.category}|{f.title[:120]}"
        if key not in key_map:
            key_map[key] = f.model_copy(deep=True)
        else:
            cur = key_map[key]
            for s in f.sources:
                if s not in cur.sources:
                    cur.sources.append(s)
            if f.severity == "Critical" or (
                cur.severity != "Critical" and f.severity == "High" and cur.severity not in ("Critical",)
            ):
                if _rank(f.severity) > _rank(cur.severity):
                    cur.severity = f.severity
            cur.mvp_bucket = merge_mvp_bucket(cur.mvp_bucket or "", f.mvp_bucket or "")
    return list(key_map.values())


def _rank(s: str) -> int:
    return {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}.get(s, 0)
