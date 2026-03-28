"""semgrep scan — 多语言 SAST。"""

from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path) -> Dict[str, Any]:
    exe = base.which_or_skip("semgrep")
    if not exe:
        return base.write_skip(out_json, "semgrep", "CLI not found")
    r = base.run_cmd(
        [exe, "--config", "auto", "--json", "--output", str(out_json), ".", "--quiet"],
        cwd=repo,
        timeout=900,
    )
    if not out_json.is_file():
        out_json.write_text('{"results":[],"errors":[],"paths":{}}', encoding="utf-8")
        if r.returncode != 0:
            return base.write_raw_error(out_json, "semgrep", r.stderr or r.stdout or "", r.returncode)
    return {"ok": True, "path": str(out_json)}
