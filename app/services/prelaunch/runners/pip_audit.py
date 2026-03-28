"""pip-audit — Python 依赖 CVE（需可访问漏洞库，可能走网络）。"""

from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path) -> Dict[str, Any]:
    exe = base.which_or_skip("pip-audit")
    if not exe:
        return base.write_skip(out_json, "pip_audit", "CLI not found")
    req = repo / "requirements.txt"
    if not req.is_file():
        return base.write_skip(
            out_json,
            "pip_audit",
            "no requirements.txt (MVP 仅扫描 requirements.txt；pyproject 项目可生成锁定 requirements 后再扫)",
        )
    cmd = [exe, "-r", str(req), "-f", "json", "-o", str(out_json), "--progress-spinner", "off"]
    r = base.run_cmd(cmd, cwd=repo, timeout=600)
    if not out_json.is_file():
        base.write_skip(out_json, "pip_audit", r.stderr or "no output file")
        return {"ok": False, "path": str(out_json), "error": (r.stderr or "")[:500]}
    # pip-audit 在无漏洞时也可能 exit 0
    return {"ok": True, "path": str(out_json)}
