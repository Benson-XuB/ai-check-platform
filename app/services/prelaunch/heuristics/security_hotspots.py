"""明显安全热点（保守规则）：Spring permitAll、CSRF 关闭等。"""

import re
from pathlib import Path
from typing import List

from app.services.prelaunch.detect import ProjectProfile
from app.services.prelaunch.heuristics._walk import iter_text_files
from app.services.prelaunch.parsers.util import finding_id
from app.services.prelaunch.schemas import NormalizedFinding

_RE_CSRF_DISABLE = re.compile(r"csrf\s*\(\s*\)\s*\.\s*disable\s*\(\s*\)", re.IGNORECASE)


def scan(repo: Path, profile: ProjectProfile) -> List[NormalizedFinding]:
    if not profile.has_java:
        return []
    repo = repo.resolve()
    findings: List[NormalizedFinding] = []

    for path in iter_text_files(repo):
        if path.suffix.lower() != ".java":
            continue
        rel = str(path.relative_to(repo)).replace("\\", "/")
        if "test" in rel.lower():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()

        if "Security" in path.name or "security" in rel.lower() or "WebSecurity" in text or "HttpSecurity" in text:
            if "permitAll()" in text and ("authorizeHttpRequests" in text or "SecurityFilterChain" in text):
                line_no = _find_line(lines, "permitAll") or 1
                findings.append(
                    NormalizedFinding(
                        id=finding_id(rel, line_no, "heuristic:permit_all"),
                        severity="High",
                        category="sast",
                        title="Spring Security 配置中出现 permitAll（需确认是否暴露敏感接口）",
                        file=rel,
                        line=line_no,
                        snippet=_snippet(lines, line_no),
                        sources=["heuristic_security"],
                        raw_refs={"rule": "permit_all"},
                        mvp_bucket="later",
                    )
                )
            if _RE_CSRF_DISABLE.search(text):
                line_no = _find_line(lines, "csrf") or 1
                findings.append(
                    NormalizedFinding(
                        id=finding_id(rel, line_no, "heuristic:csrf_disable"),
                        severity="Medium",
                        category="sast",
                        title="已禁用 CSRF（若含 cookie 会话需评估风险）",
                        file=rel,
                        line=line_no,
                        snippet=_snippet(lines, line_no),
                        sources=["heuristic_security"],
                        raw_refs={"rule": "csrf_disable"},
                        mvp_bucket="later",
                    )
                )

    return findings


def _find_line(lines: List[str], needle: str) -> int:
    for i, line in enumerate(lines, start=1):
        if needle in line:
            return i
    return 0


def _snippet(lines: List[str], line_no: int) -> str:
    if line_no < 1 or line_no > len(lines):
        return ""
    return lines[line_no - 1].strip()[:240]
