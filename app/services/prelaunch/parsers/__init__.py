"""各工具 JSON → NormalizedFinding。"""

from pathlib import Path
from typing import List

from app.services.prelaunch.parsers import bandit as bandit_p
from app.services.prelaunch.parsers import gitleaks as gitleaks_p
from app.services.prelaunch.parsers import npm as npm_p
from app.services.prelaunch.parsers import pip_audit as pip_audit_p
from app.services.prelaunch.parsers import semgrep as semgrep_p
from app.services.prelaunch.parsers import trivy as trivy_p
from app.services.prelaunch.schemas import NormalizedFinding


def parse_all(job_dir: Path) -> List[NormalizedFinding]:
    out: List[NormalizedFinding] = []
    m = [
        ("raw_gitleaks.json", gitleaks_p.parse_file),
        ("raw_semgrep.json", semgrep_p.parse_file),
        ("raw_bandit.json", bandit_p.parse_file),
        ("raw_npm_audit.json", npm_p.parse_file),
        ("raw_pip_audit.json", pip_audit_p.parse_file),
        ("raw_trivy.json", trivy_p.parse_file),
    ]
    for name, fn in m:
        p = job_dir / name
        if p.is_file():
            out.extend(fn(p))
    return out
