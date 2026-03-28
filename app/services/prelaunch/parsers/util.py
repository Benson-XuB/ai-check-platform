"""解析共用。"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_skipped_payload(data: Any) -> bool:
    return isinstance(data, dict) and data.get("skipped") is True


def finding_id(file: str, line: int, rule: str) -> str:
    raw = f"{file}|{line}|{rule}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def sev_map_gitleaks(s: str) -> str:
    return "High" if s else "Medium"


def sev_map_bandit(s: str) -> str:
    m = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
    return m.get((s or "").upper(), "Medium")


def sev_map_semgrep(extra: Dict[str, Any]) -> str:
    sev = (extra.get("severity") or "").upper()
    if sev == "ERROR":
        return "High"
    if sev == "WARNING":
        return "Medium"
    if sev == "INFO":
        return "Low"
    return "Medium"
