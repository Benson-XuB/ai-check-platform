"""解析 gitleaks JSON。"""

from pathlib import Path
from typing import Any, List

from app.services.prelaunch.parsers import util
from app.services.prelaunch.schemas import NormalizedFinding


def parse_file(path: Path) -> List[NormalizedFinding]:
    data = util.load_json(path)
    if data is None or util.is_skipped_payload(data):
        return []
    rows: List[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("results") or data.get("Results") or []
    else:
        return []
    out: List[NormalizedFinding] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("RuleID") or item.get("ruleID") or item.get("Description") or "gitleaks")
        file = str(item.get("File") or item.get("file") or "")
        line = int(item.get("StartLine") or item.get("startLine") or item.get("Line") or 0)
        title = f"Secret / 敏感信息: {rule}"
        fid = util.finding_id(file, line, rule)
        out.append(
            NormalizedFinding(
                id=fid,
                severity="Critical",
                category="secret",
                title=title,
                file=file,
                line=line,
                snippet=str(item.get("Secret") or item.get("Match") or "")[:500],
                sources=["gitleaks"],
                raw_refs={"rule": rule},
            )
        )
    return out
