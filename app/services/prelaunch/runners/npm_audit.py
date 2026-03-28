"""npm / pnpm / yarn audit。"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict

from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.runners import base


def run(repo: Path, out_json: Path, profile: ProjectProfile) -> Dict[str, Any]:
    if not (repo / "package.json").is_file():
        return base.write_skip(out_json, "npm_audit", "no package.json")

    lock = profile.lockfiles
    if "pnpm-lock.yaml" in lock and shutil.which("pnpm"):
        cmd = ["pnpm", "audit", "--json"]
        r = base.run_cmd(cmd, cwd=repo, timeout=600)
    elif "yarn.lock" in lock and shutil.which("yarn"):
        cmd = ["yarn", "audit", "--json"]
        r = base.run_cmd(cmd, cwd=repo, timeout=600)
    elif shutil.which("npm"):
        cmd = ["npm", "audit", "--json"]
        r = base.run_cmd(cmd, cwd=repo, timeout=600)
    else:
        return base.write_skip(out_json, "npm_audit", "npm/pnpm/yarn not found")

    out_json.write_text(r.stdout or "{}", encoding="utf-8")
    if r.returncode not in (0, 1) and not r.stdout.strip():
        return base.write_raw_error(out_json, "npm_audit", r.stderr or "empty stdout", r.returncode)
    try:
        json.loads(out_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        out_json.write_text(json.dumps({"parse_error": True, "raw": (r.stdout or "")[:5000]}), encoding="utf-8")
    return {"ok": True, "path": str(out_json)}
