"""bandit — Python 安全。"""

from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path) -> Dict[str, Any]:
    exe = base.which_or_skip("bandit")
    if not exe:
        return base.write_skip(out_json, "bandit", "CLI not found")
    r = base.run_cmd(
        [
            exe,
            "-r",
            ".",
            "-f",
            "json",
            "-o",
            str(out_json),
            "-x",
            ".git,node_modules,venv,.venv,dist,build,__pycache__",
            "-q",
        ],
        cwd=repo,
        timeout=600,
    )
    if not out_json.is_file():
        out_json.write_text('{"results":[],"errors":[]}', encoding="utf-8")
        if r.returncode not in (0, 1):
            return base.write_raw_error(out_json, "bandit", r.stderr or r.stdout or "", r.returncode)
    return {"ok": True, "path": str(out_json)}
