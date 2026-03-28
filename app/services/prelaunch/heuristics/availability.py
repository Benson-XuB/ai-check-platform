"""轻量可用性信号：吞异常、空 catch 等（仅明显模式）。"""

import re
from pathlib import Path
from typing import List

from app.services.prelaunch.heuristics._walk import iter_text_files
from app.services.prelaunch.parsers.util import finding_id
from app.services.prelaunch.schemas import NormalizedFinding

_RE_PY_BARE_EXCEPT_PASS = re.compile(r"(?m)^(\s*)except\s*:\s*\n\s*\1pass\s*(?:#.*)?$")
_RE_PY_EXCEPT_PASS = re.compile(r"(?m)^(\s*)except\s+[^:\n]+:\s*\n\s*\1pass\s*(?:#.*)?$")
_RE_JS_EMPTY_CATCH = re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")


def scan(repo: Path) -> List[NormalizedFinding]:
    repo = repo.resolve()
    findings: List[NormalizedFinding] = []

    for path in iter_text_files(repo):
        rel = str(path.relative_to(repo)).replace("\\", "/")
        suf = path.suffix.lower()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()

        if suf == ".py":
            if _RE_PY_BARE_EXCEPT_PASS.search(text):
                line_no = _line_of_match(lines, r"except\s*:") or 1
                findings.append(
                    NormalizedFinding(
                        id=finding_id(rel, line_no, "heuristic:bare_except_pass"),
                        severity="Medium",
                        category="availability",
                        title="Python 裸 except: 且仅 pass（易吞掉错误）",
                        file=rel,
                        line=line_no,
                        snippet=_snippet(lines, line_no),
                        sources=["heuristic_availability"],
                        raw_refs={"rule": "bare_except_pass"},
                        mvp_bucket="later",
                    )
                )
            elif _RE_PY_EXCEPT_PASS.search(text):
                line_no = _line_of_match(lines, r"except\s+") or 1
                findings.append(
                    NormalizedFinding(
                        id=finding_id(rel, line_no, "heuristic:except_pass"),
                        severity="Low",
                        category="availability",
                        title="except 块仅 pass（可能隐藏失败原因）",
                        file=rel,
                        line=line_no,
                        snippet=_snippet(lines, line_no),
                        sources=["heuristic_availability"],
                        raw_refs={"rule": "except_pass"},
                        mvp_bucket="info",
                    )
                )

        if suf in (".js", ".jsx", ".ts", ".tsx") and _RE_JS_EMPTY_CATCH.search(text):
            line_no = _line_of_match(lines, r"catch\s*\(") or 1
            findings.append(
                NormalizedFinding(
                    id=finding_id(rel, line_no, "heuristic:empty_catch"),
                    severity="Low",
                    category="availability",
                    title="空的 catch 块（错误可能被静默吞掉）",
                    file=rel,
                    line=line_no,
                    snippet=_snippet(lines, line_no),
                    sources=["heuristic_availability"],
                    raw_refs={"rule": "empty_catch"},
                    mvp_bucket="info",
                )
            )

    return findings


def _line_of_match(lines: List[str], pattern: str) -> int:
    rx = re.compile(pattern)
    for i, line in enumerate(lines, start=1):
        if rx.search(line):
            return i
    return 0


def _snippet(lines: List[str], line_no: int) -> str:
    if line_no < 1 or line_no > len(lines):
        return ""
    return lines[line_no - 1].strip()[:240]
