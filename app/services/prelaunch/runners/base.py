"""子进程通用封装。"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def which_or_skip(name: str) -> Optional[str]:
    return shutil.which(name)


def run_cmd(
    args: List[str],
    cwd: Path,
    timeout: int = 900,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def write_skip(out_path: Path, tool: str, reason: str) -> Dict[str, Any]:
    payload = {"skipped": True, "tool": tool, "reason": reason}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"skipped": True, "path": str(out_path), "reason": reason}


def write_raw_error(out_path: Path, tool: str, stderr: str, code: int) -> Dict[str, Any]:
    payload = {"ok": False, "tool": tool, "exit_code": code, "stderr": stderr[:8000]}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": False, "path": str(out_path), "error": stderr[:500]}
