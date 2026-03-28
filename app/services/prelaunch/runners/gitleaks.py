"""gitleaks detect — 密钥扫描。"""

import json
from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path) -> Dict[str, Any]:
    exe = base.which_or_skip("gitleaks")
    if not exe:
        return base.write_skip(out_json, "gitleaks", "CLI not found")
    # gitleaks v8: detect --source . --report-path <f> --report-format json
    r = base.run_cmd(
        [exe, "detect", "--source", ".", "--report-path", str(out_json), "--report-format", "json", "--exit-code", "0"],
        cwd=repo,
        timeout=600,
    )
    if not out_json.is_file():
        out_json.write_text("[]", encoding="utf-8")
        if r.returncode != 0 and r.stderr:
            return base.write_raw_error(out_json, "gitleaks", r.stderr, r.returncode)
    return {"ok": True, "path": str(out_json)}
