"""解析 pip-audit JSON。"""

from pathlib import Path
from typing import List

from app.services.prelaunch.parsers import util
from app.services.prelaunch.schemas import NormalizedFinding


def parse_file(path: Path) -> List[NormalizedFinding]:
    data = util.load_json(path)
    if data is None or util.is_skipped_payload(data):
        return []
    deps = data.get("dependencies")
    if not isinstance(deps, list):
        return []
    out: List[NormalizedFinding] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name = str(dep.get("name") or "")
        ver = str(dep.get("version") or "")
        vulns = dep.get("vulns") or []
        if not isinstance(vulns, list) or not vulns:
            continue
        for v in vulns:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("id") or v.get("name") or "pip-audit")
            desc = str(v.get("description") or vid)[:500]
            sev = str(v.get("severity") or "High").capitalize()
            if sev not in ("Critical", "High", "Medium", "Low", "Info"):
                sev = "High"
            title = f"Python 依赖: {name}@{ver} — {vid}"
            fid = util.finding_id(f"pip:{name}", 0, vid)
            out.append(
                NormalizedFinding(
                    id=fid,
                    severity=sev,
                    category="dependency",
                    title=title[:500],
                    file="(python dependencies)",
                    line=0,
                    snippet=desc,
                    sources=["pip_audit"],
                    raw_refs={"package": name, "version": ver, "id": vid},
                )
            )
    return out
