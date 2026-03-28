"""检测本机是否具备各扫描 CLI（不保证版本兼容，仅提示运维）。"""

import shutil
import subprocess
from typing import Any, Dict, List, Optional

from app.services.prelaunch.config import REQUIRED_SCANNER_HINTS

SCANNERS = ("gitleaks", "semgrep", "bandit", "pip-audit", "npm", "trivy")


def _probe_version(binary: str) -> Optional[str]:
    path = shutil.which(binary)
    if not path:
        return None
    for args in ((binary, "--version"), (binary, "-version")):
        try:
            r = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            out = (r.stdout or r.stderr or "").strip()
            if out:
                return out.split("\n")[0][:240]
        except (OSError, subprocess.TimeoutExpired):
            continue
    return "unknown"


def scanner_status() -> Dict[str, Any]:
    scanners: List[Dict[str, Any]] = []
    for name in SCANNERS:
        path = shutil.which(name)
        scanners.append(
            {
                "name": name,
                "available": path is not None,
                "path": path,
                "version": _probe_version(name) if path else None,
            }
        )
    return {"scanners": scanners, "hints": list(REQUIRED_SCANNER_HINTS)}
