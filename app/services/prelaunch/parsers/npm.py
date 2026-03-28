"""解析 npm audit JSON（结构因版本而异，做宽松解析）。"""

from pathlib import Path
from typing import List

from app.services.prelaunch.parsers import util
from app.services.prelaunch.schemas import NormalizedFinding


def parse_file(path: Path) -> List[NormalizedFinding]:
    data = util.load_json(path)
    if data is None or util.is_skipped_payload(data):
        return []
    out: List[NormalizedFinding] = []
    vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
    if isinstance(vulns, dict):
        for name, meta in vulns.items():
            if not isinstance(meta, dict):
                continue
            sev = str(meta.get("severity") or "medium").capitalize()
            if sev.lower() == "critical":
                sev = "Critical"
            elif sev.lower() == "high":
                sev = "High"
            elif sev.lower() == "moderate":
                sev = "Medium"
            elif sev.lower() == "low":
                sev = "Low"
            else:
                sev = "Medium"
            title = f"依赖漏洞: {name}"
            via = meta.get("via")
            detail = ""
            if isinstance(via, list) and via:
                v0 = via[0]
                if isinstance(v0, dict):
                    detail = str(v0.get("title") or v0.get("name") or "")[:400]
            fid = util.finding_id("package.json", 0, f"npm:{name}")
            out.append(
                NormalizedFinding(
                    id=fid,
                    severity=sev,
                    category="dependency",
                    title=title,
                    file="package.json",
                    line=0,
                    snippet=detail,
                    sources=["npm_audit"],
                    raw_refs={"package": name},
                )
            )
    return out
