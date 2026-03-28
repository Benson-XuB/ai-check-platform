"""仓库静态启发式（配置、可用性、明显安全热点），产出 NormalizedFinding。"""

from pathlib import Path
from typing import List

from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.heuristics import availability as avail_m
from app.services.prelaunch.heuristics import config as config_m
from app.services.prelaunch.heuristics import security_hotspots as sec_m
from app.services.prelaunch.schemas import NormalizedFinding


def run_repo_heuristics(repo_root: Path, profile: ProjectProfile) -> List[NormalizedFinding]:
    repo_root = repo_root.resolve()
    out: List[NormalizedFinding] = []
    out.extend(config_m.scan(repo_root))
    out.extend(avail_m.scan(repo_root))
    out.extend(sec_m.scan(repo_root, profile))
    return out
