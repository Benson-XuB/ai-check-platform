"""为扫描器结果补全 mvp_bucket；依赖类仅将 Critical/High 视为上线阻断候选。"""

from typing import List

from app.services.prelaunch.schemas import NormalizedFinding

_BUCKET_RANK = {"blocking": 3, "later": 2, "info": 1, "": 0}


def _rank_sev(s: str) -> int:
    return {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}.get(s, 0)


def classify_finding(f: NormalizedFinding) -> str:
    if f.mvp_bucket in _BUCKET_RANK and _BUCKET_RANK[f.mvp_bucket] > 0:
        return f.mvp_bucket
    cat = f.category
    sev = f.severity
    r = _rank_sev(sev)

    if cat == "secret":
        return "blocking" if r >= 3 else "later" if r == 2 else "info"
    if cat == "dependency":
        return "blocking" if r >= 3 else "later" if r == 2 else "info"
    if cat == "sast":
        return "blocking" if r >= 3 else "later" if r >= 2 else "info"
    if cat == "config":
        return "blocking" if r >= 3 else "later" if r >= 2 else "info"
    if cat == "availability":
        return "later" if r >= 2 else "info"
    return "later" if r >= 3 else "info" if r <= 1 else "later"


def apply_mvp_buckets(findings: List[NormalizedFinding]) -> List[NormalizedFinding]:
    for f in findings:
        f.mvp_bucket = classify_finding(f)
    return findings


def merge_mvp_bucket(a: str, b: str) -> str:
    """去重合并时取更严格的一档；双方均未标 bucket 时保持空（由 apply_mvp_buckets 补全）。"""
    ra, rb = _BUCKET_RANK.get(a or "", 0), _BUCKET_RANK.get(b or "", 0)
    if ra == 0 and rb == 0:
        return ""
    if ra == 0:
        return b or ""
    if rb == 0:
        return a or ""
    return a if ra >= rb else b
