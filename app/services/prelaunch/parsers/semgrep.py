"""解析 semgrep JSON。"""

from pathlib import Path
from typing import Any, List

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
        check_id = str(item.get("check_id") or "semgrep")
        path_s = str(item.get("path") or "")
        start = item.get("start") or {}
        line = int(start.get("line") or 0)
        extra = item.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        title = str(extra.get("message") or check_id)[:500]
        sev = util.sev_map_semgrep(extra)
        snippet = ""
        lines = extra.get("lines") or {}
        if isinstance(lines, dict):
            snippet = str(lines.get("snippet") or "")[:500]
        fid = util.finding_id(path_s, line, check_id)
        out.append(
            NormalizedFinding(
                id=fid,
                severity=sev,
                category="sast",
                title=title,
                file=path_s,
                line=line,
                snippet=snippet,
                sources=["semgrep"],
                raw_refs={"check_id": check_id},
            )
        )
    return out
