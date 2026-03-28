"""环境 & 配置启发式：debug、.env 入仓、CORS 过宽、生产配置中的 localhost API。"""

import re
from pathlib import Path
from typing import List

from app.services.prelaunch.heuristics._walk import iter_text_files
from app.services.prelaunch.parsers.util import finding_id
from app.services.prelaunch.schemas import NormalizedFinding

# 允许提交的 env 模板名（小写）
_ENV_ALLOWLIST = frozenset(
    {
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.dist",
        ".env.local.example",
    }
)

_RE_DEBUG_PY = re.compile(r"(?:^|\n)\s*DEBUG\s*=\s*True\b", re.IGNORECASE)
_RE_FLASK_RUN = re.compile(r"app\.run\([^)]*\bdebug\s*=\s*True\b", re.IGNORECASE)
_RE_DEBUG_YML = re.compile(r"(?m)^\s*debug\s*:\s*true\s*$", re.IGNORECASE)
_RE_ENV_DEBUG = re.compile(r"(?m)^\s*(?:FLASK_)?DEBUG\s*=\s*(?:1|true|yes|on)\s*$", re.IGNORECASE)
_RE_CORS_STAR = re.compile(
    r"(allow_origins\s*=\s*\[\s*[\"']\*\s*[\"']\]|"
    r"Access-Control-Allow-Origin[\"']?\s*[:=]\s*[\"']?\*|"
    r"cors\([^)]*origin\s*:\s*[\"']\s*\*\s*[\"']|"
    r"origin\s*:\s*[\"']\s*\*\s*[\"'])",
    re.IGNORECASE,
)
_RE_LOCALHOST_API = re.compile(
    r"(?:API_URL|BASE_URL|VITE_\w+|NEXT_PUBLIC_\w+|REACT_APP_\w+)\s*[=:]\s*[\"']?https?://(?:127\.0\.0\.1|localhost)(?::\d+)?",
    re.IGNORECASE,
)


def _is_prodish_path(rel: str) -> bool:
    s = rel.lower()
    return any(
        x in s
        for x in (
            "production",
            "prod.",
            ".env.production",
            "application-prod",
            "application-production",
            "settings_prod",
        )
    )


def scan(repo: Path) -> List[NormalizedFinding]:
    repo = repo.resolve()
    findings: List[NormalizedFinding] = []

    for path in iter_text_files(repo):
        rel = str(path.relative_to(repo)).replace("\\", "/")
        low = path.name.lower()

        # .env 真文件入仓（非模板）
        if low == ".env" or (low.startswith(".env.") and low not in _ENV_ALLOWLIST):
            seen_dotenv = True
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, 0, "heuristic:dotenv_in_repo"),
                    severity="High",
                    category="config",
                    title="仓库中存在 .env 类文件（可能含密钥，通常不应提交）",
                    file=rel,
                    line=0,
                    snippet="请确认是否误提交；应使用 .env.example 等模板。",
                    sources=["heuristic_config"],
                    raw_refs={"rule": "dotenv_in_repo"},
                    mvp_bucket="blocking",
                )
            )
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = text.splitlines()
        prodish = _is_prodish_path(rel)

        if _RE_DEBUG_PY.search(text) or _RE_FLASK_RUN.search(text):
            line_no = _first_match_line(lines, _RE_DEBUG_PY) or _first_match_line(lines, _RE_FLASK_RUN) or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:debug_true"),
                    severity="High" if prodish else "Medium",
                    category="config",
                    title="检测到 Debug 开启（Python）",
                    file=rel,
                    line=line_no,
                    snippet=_line_snippet(lines, line_no),
                    sources=["heuristic_config"],
                    raw_refs={"rule": "debug_true"},
                    mvp_bucket="blocking" if prodish else "later",
                )
            )

        if path.suffix.lower() in (".yml", ".yaml") and _RE_DEBUG_YML.search(text):
            line_no = _first_match_line(lines, _RE_DEBUG_YML) or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:yaml_debug"),
                    severity="High" if prodish else "Medium",
                    category="config",
                    title="YAML 中 debug: true",
                    file=rel,
                    line=line_no,
                    snippet=_line_snippet(lines, line_no),
                    sources=["heuristic_config"],
                    raw_refs={"rule": "yaml_debug"},
                    mvp_bucket="blocking" if prodish else "later",
                )
            )

        if low.startswith(".env") and _RE_ENV_DEBUG.search(text):
            line_no = _first_match_line(lines, _RE_ENV_DEBUG) or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:env_debug"),
                    severity="High" if "production" in low else "Medium",
                    category="config",
                    title=".env 类文件中开启 DEBUG",
                    file=rel,
                    line=line_no,
                    snippet=_line_snippet(lines, line_no),
                    sources=["heuristic_config"],
                    raw_refs={"rule": "env_debug"},
                    mvp_bucket="blocking" if "production" in low else "later",
                )
            )

        if _RE_CORS_STAR.search(text):
            line_no = _first_match_line(lines, _RE_CORS_STAR) or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:cors_star"),
                    severity="High",
                    category="config",
                    title="CORS 允许任意来源（*）",
                    file=rel,
                    line=line_no,
                    snippet=_line_snippet(lines, line_no),
                    sources=["heuristic_config"],
                    raw_refs={"rule": "cors_star"},
                    mvp_bucket="blocking",
                )
            )

        if prodish and _RE_LOCALHOST_API.search(text):
            line_no = _first_match_line(lines, _RE_LOCALHOST_API) or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:localhost_api_url"),
                    severity="Medium",
                    category="config",
                    title="生产向配置中出现 localhost/127.0.0.1 API 地址",
                    file=rel,
                    line=line_no,
                    snippet=_line_snippet(lines, line_no),
                    sources=["heuristic_config"],
                    raw_refs={"rule": "localhost_api_url"},
                    mvp_bucket="later",
                )
            )

    return findings


def _first_match_line(lines: List[str], rx: re.Pattern) -> int:
    for i, line in enumerate(lines, start=1):
        if rx.search(line):
            return i
    return 0


def _line_snippet(lines: List[str], line_no: int) -> str:
    if line_no < 1 or line_no > len(lines):
        return ""
    return lines[line_no - 1].strip()[:240]
