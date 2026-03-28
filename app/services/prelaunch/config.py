"""Prelaunch 工作区与环境变量。"""

import os
from pathlib import Path

# 运维需在宿主机或镜像内安装以下 CLI（版本以各工具文档为准）：
# - gitleaks: gitleaks version
# - semgrep: semgrep --version
# - bandit: bandit --version  (Python)
# - npm: npm --version  (需 Node)
# - trivy: trivy --version
REQUIRED_SCANNER_HINTS = (
    "gitleaks — 密钥扫描；https://github.com/gitleaks/gitleaks",
    "semgrep — 多语言 SAST；https://semgrep.dev",
    "bandit — Python 安全；https://bandit.readthedocs.io",
    "npm — Node 依赖审计（随 Node 安装）",
    "trivy — 依赖/文件系统漏洞；https://aquasecurity.github.io/trivy",
    "pip-audit — Python requirements.txt 依赖 CVE；https://pypi.org/project/pip-audit",
)

_DEFAULT_WORKSPACE_NAME = ".prelaunch_workspace"
_DEFAULT_TTL_HOURS = 24
_DEFAULT_MAX_REPO_MB = 500


def get_workspace_root() -> Path:
    """扫描产物与 clone 目录根；默认 <cwd>/.prelaunch_workspace。"""
    raw = os.getenv("PRELAUNCH_WORKSPACE", "").strip()
    root = Path(raw).expanduser() if raw else (Path.cwd() / _DEFAULT_WORKSPACE_NAME)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def get_job_ttl_hours() -> int:
    try:
        return max(1, int(os.getenv("PRELAUNCH_JOB_TTL_HOURS", str(_DEFAULT_TTL_HOURS))))
    except ValueError:
        return _DEFAULT_TTL_HOURS


def get_max_repo_mb() -> int:
    try:
        return max(50, int(os.getenv("PRELAUNCH_MAX_REPO_MB", str(_DEFAULT_MAX_REPO_MB))))
    except ValueError:
        return _DEFAULT_MAX_REPO_MB
