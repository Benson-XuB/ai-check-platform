"""trivy fs — 文件系统/依赖漏洞。"""

from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path) -> Dict[str, Any]:
    exe = base.which_or_skip("trivy")
    if not exe:
        return base.write_skip(out_json, "trivy", "CLI not found")
    r = base.run_cmd(
        [exe, "fs", "--scanners", "vuln", "--format", "json", "--output", str(out_json), "."],
        cwd=repo,
        timeout=1200,
    )
    if not out_json.is_file():
        out_json.write_text('{"Results":[]}', encoding="utf-8")
        if r.returncode != 0:
            return base.write_raw_error(out_json, "trivy", r.stderr or r.stdout or "", r.returncode)
    return {"ok": True, "path": str(out_json)}
