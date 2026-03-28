"""扫描器子进程封装。"""

from pathlib import Path
from typing import Any, Dict, List

from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.runners import bandit as bandit_r
from app.services.prelaunch.runners import gitleaks as gitleaks_r
from app.services.prelaunch.runners import npm_audit as npm_r
from app.services.prelaunch.runners import pip_audit as pip_audit_r
from app.services.prelaunch.runners import semgrep as semgrep_r
from app.services.prelaunch.runners import trivy as trivy_r


def run_all_scanners(repo: Path, job_dir: Path, profile: ProjectProfile) -> Dict[str, Any]:
    """依次运行各工具，结果写入 job_dir/raw_*.json。"""
    results: Dict[str, Any] = {}
    repo = repo.resolve()
    job_dir.mkdir(parents=True, exist_ok=True)

    results["gitleaks"] = gitleaks_r.run(repo, job_dir / "raw_gitleaks.json")
    results["semgrep"] = semgrep_r.run(repo, job_dir / "raw_semgrep.json")

    if profile.has_python:
        results["bandit"] = bandit_r.run(repo, job_dir / "raw_bandit.json")
    else:
        results["bandit"] = {"skipped": True, "reason": "no_python"}

    if profile.has_python and (repo / "requirements.txt").is_file():
        results["pip_audit"] = pip_audit_r.run(repo, job_dir / "raw_pip_audit.json")
    else:
        results["pip_audit"] = {"skipped": True, "reason": "no_python_lockfiles"}

    if profile.has_node and (repo / "package.json").is_file():
        results["npm_audit"] = npm_r.run(repo, job_dir / "raw_npm_audit.json", profile)
    else:
        results["npm_audit"] = {"skipped": True, "reason": "no_package_json"}

    results["trivy"] = trivy_r.run(repo, job_dir / "raw_trivy.json")

    return results
