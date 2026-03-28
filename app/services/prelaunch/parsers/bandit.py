"""解析 bandit JSON。"""

from pathlib import Path
from typing import List

from app.services.prelaunch.parsers import util
from app.services.prelaunch.schemas import NormalizedFinding


def parse_file(path: Path) -> List[NormalizedFinding]:
    data = util.load_json(path)
    if data is None or util.is_skipped_payload(data):
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    out: List[NormalizedFinding] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        test_id = str(item.get("test_id") or "bandit")
        file = str(item.get("filename") or "")
        line = int(item.get("line_number") or 0)
        title = str(item.get("issue_text") or test_id)[:500]
        sev = util.sev_map_bandit(str(item.get("issue_severity") or ""))
        snippet = str(item.get("code") or "")[:500]
        fid = util.finding_id(file, line, test_id)
        out.append(
            NormalizedFinding(
                id=fid,
                severity=sev,
                category="sast",
                title=title,
                file=file,
                line=line,
                snippet=snippet,
                sources=["bandit"],
                raw_refs={"test_id": test_id},
            )
        )
    return out
