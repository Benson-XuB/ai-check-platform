"""
Tree-sitter based analyzer (v1).

Responsibilities:
- Parse a file to extract symbols (function/class names) for Python; Vue/JS symbols later.
- For a unified diff, estimate whether changes are comment-only vs logic-like.

Note: We don't do full AST diff between base/head yet. This is an incremental step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover
    get_parser = None


@dataclass(frozen=True)
class ChangeSummary:
    change_kind: str  # comment_only | import_change | signature_change | logic_change | unknown
    changed_symbols: List[str]


_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _iter_diff_added_removed_lines(diff: str, path: str) -> List[str]:
    """
    Extract +/- lines for a file from a unified diff.
    Returns raw lines including leading +/-, excluding +++/--- headers.
    """
    lines = diff.split("\n")
    cur_file = ""
    out: List[str] = []
    for line in lines:
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            cur_file = p
            continue
        if not cur_file:
            continue
        if cur_file != path and not cur_file.endswith("/" + path):
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+") or line.startswith("-"):
            if line.startswith("+++") or line.startswith("---"):
                continue
            out.append(line)
    return out


def _is_comment_or_whitespace_line(line: str, file_ext: str) -> bool:
    s = line.strip()
    if not s:
        return True
    # strip diff prefix if present
    if s[0] in "+-":
        s = s[1:].lstrip()
    if not s:
        return True
    if file_ext == ".py":
        return s.startswith("#")
    if file_ext in (".js", ".ts", ".vue"):
        return s.startswith("//") or s.startswith("/*") or s.startswith("*") or s.startswith("*/")
    return False


def classify_change_kind(diff: str, changed_files: List[str]) -> Dict[str, str]:
    """
    For each file, classify change kind with a lightweight heuristic:
    - If all +/- lines are comment/whitespace => comment_only
    - If import-only lines (python import/from, js import/require) => import_change
    - Otherwise => logic_change
    """
    kinds: Dict[str, str] = {}
    for path in changed_files:
        ext = "." + path.rsplit(".", 1)[1] if "." in path else ""
        delta = _iter_diff_added_removed_lines(diff, path)
        if not delta:
            continue
        if all(_is_comment_or_whitespace_line(l, ext) for l in delta):
            kinds[path] = "comment_only"
            continue
        stripped = []
        for l in delta:
            t = l[1:].strip() if l and l[0] in "+-" else l.strip()
            if t:
                stripped.append(t)
        if ext == ".py" and stripped and all(t.startswith("import ") or t.startswith("from ") for t in stripped):
            kinds[path] = "import_change"
            continue
        if ext in (".js", ".ts", ".vue") and stripped and all(
            t.startswith("import ") or "require(" in t for t in stripped
        ):
            kinds[path] = "import_change"
            continue
        kinds[path] = "logic_change"
    return kinds


def extract_python_symbols(path: str, content: str) -> List[str]:
    """
    Extract top-level function/class names using tree-sitter-python.
    """
    if get_parser is None:
        return []
    try:
        parser = get_parser("python")
        tree = parser.parse(bytes(content, "utf8"))
    except TypeError:
        # tree_sitter_languages is commonly incompatible with tree-sitter>=0.22.
        # Pin tree-sitter==0.21.3 for compatibility.
        return []
    root = tree.root_node
    symbols: Set[str] = set()
    for child in root.children:
        if child.type in ("function_definition", "class_definition"):
            # Find identifier child
            for c2 in child.children:
                if c2.type == "identifier":
                    name = content[c2.start_byte : c2.end_byte]
                    if name:
                        symbols.add(name)
    return sorted(symbols)


def summarize_changes(diff: str, file_contexts: Dict[str, str], changed_files: List[str]) -> ChangeSummary:
    """
    Summarize overall change kind and changed symbols (Python only v1).
    """
    per_file_kind = classify_change_kind(diff, changed_files)
    overall = "comment_only" if per_file_kind and all(v == "comment_only" for v in per_file_kind.values()) else "logic_change"
    symbols: Set[str] = set()
    for path in changed_files:
        if not path.endswith(".py"):
            continue
        content = file_contexts.get(path)
        if not content:
            continue
        for s in extract_python_symbols(path, content):
            symbols.add(s)
    return ChangeSummary(change_kind=overall, changed_symbols=sorted(symbols))

