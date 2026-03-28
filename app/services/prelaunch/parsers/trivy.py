"""解析 trivy fs JSON。"""

from pathlib import Path
from typing import Any, List

from app.services.prelaunch.parsers import util
from app.services.prelaunch.schemas import NormalizedFinding


def _sev(s: str) -> str:
    u = (s or "").upper()
    if u in ("CRITICAL",):
        return "Critical"
    if u in ("HIGH",):
        return "High"
    if u in ("MEDIUM",):
        return "Medium"
    if u in ("LOW",):
        return "Low"
    return "Medium"


def parse_file(path: Path) -> List[NormalizedFinding]:
    data = util.load_json(path)
    if data is None or util.is_skipped_payload(data):
        return []
    results = data.get("Results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    out: List[NormalizedFinding] = []
    for block in results:
        if not isinstance(block, dict):
            continue
        target = str(block.get("Target") or "")
        vulns = block.get("Vulnerabilities") or []
        if not isinstance(vulns, list):
            continue
        for v in vulns:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("VulnerabilityID") or v.get("ID") or "CVE")
            title = str(v.get("Title") or vid)[:500]
            sev = _sev(str(v.get("Severity") or ""))
            pkg = str(v.get("PkgName") or "")
            installed = str(v.get("InstalledVersion") or "")
            fid = util.finding_id(target, 0, f"{vid}:{pkg}")
            out.append(
                NormalizedFinding(
                    id=fid,
                    severity=sev,
                    category="dependency",
                    title=f"{title} ({pkg}@{installed})"[:500],
                    file=target,
                    line=0,
                    snippet=str(v.get("Description") or "")[:500],
                    sources=["trivy"],
                    raw_refs={"id": vid, "pkg": pkg},
                )
            )
    return out
