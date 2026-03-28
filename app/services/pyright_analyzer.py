"""
Pyright analyzer (Python only, sandboxed).

Approach (v1):
- Build a sandbox workspace under .sandbox/<owner>__<repo>__<sha>/
- Write available .py file_contexts into that workspace (best-effort)
- Also attempt to fetch common config files (pyproject.toml, pyrightconfig.json, etc.) if caller provides them
- Run pyright with --outputjson and parse diagnostics
- Return:
  - diagnostics summary
  - affected files (files with errors)

This runs only when Tree-sitter change_kind != comment_only.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class PyrightResult:
    ok: bool
    error: str = ""
    diagnostics: List[dict] = None  # raw pyright diagnostics
    affected_files: List[str] = None


def _sandbox_dir(owner: str, repo: str, sha: str) -> Path:
    base = Path(__file__).resolve().parents[2] / ".sandbox"
    safe = f"{owner}__{repo}__{sha}"
    return base / safe


def _write_file(root: Path, rel_path: str, content: str) -> None:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", errors="ignore")


def _filter_repo_paths(file_contexts: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path, content in file_contexts.items():
        if not path or not isinstance(path, str):
            continue
        if path.startswith("["):
            continue
        if not content:
            continue
        out[path] = content
    return out


def run_pyright_in_sandbox(
    *,
    owner: str,
    repo: str,
    sha: str,
    file_contexts: Dict[str, str],
    fetch_file: Callable[[str], Optional[str]],
    max_write_files: int = 200,
) -> PyrightResult:
    """
    Create/reuse sandbox and run pyright. Returns diagnostics and affected file paths.
    """
    root = _sandbox_dir(owner, repo, sha)
    root.mkdir(parents=True, exist_ok=True)

    repo_files = _filter_repo_paths(file_contexts)
    wrote = 0
    for path, content in repo_files.items():
        if wrote >= max_write_files:
            break
        if path.endswith(".py") or path.endswith(".pyi") or path.endswith(".toml") or path.endswith(".json") or path.endswith(".cfg"):
            _write_file(root, path, content)
            wrote += 1

    # Try to fetch common configs if missing
    for cfg in ["pyrightconfig.json", "pyproject.toml", "setup.cfg", "requirements.txt"]:
        if (root / cfg).exists():
            continue
        c = fetch_file(cfg)
        if c:
            _write_file(root, cfg, c)

    cmd = ["pyright", "--outputjson"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=45,
        )
    except FileNotFoundError:
        return PyrightResult(ok=False, error="pyright 未安装（找不到命令）", diagnostics=[], affected_files=[])
    except subprocess.TimeoutExpired:
        return PyrightResult(ok=False, error="pyright 执行超时", diagnostics=[], affected_files=[])

    stdout = proc.stdout or ""
    # pyright outputs JSON even when exit_code != 0
    try:
        obj = json.loads(stdout) if stdout.strip().startswith("{") else {}
    except json.JSONDecodeError:
        return PyrightResult(ok=False, error="pyright 输出无法解析为 JSON", diagnostics=[], affected_files=[])

    diags = obj.get("generalDiagnostics") or []
    affected: Set[str] = set()
    for d in diags:
        f = d.get("file")
        if f and isinstance(f, str):
            # normalize to repo-relative when possible
            try:
                p = Path(f)
                if p.is_absolute():
                    rel = str(p.relative_to(root))
                    affected.add(rel)
                else:
                    affected.add(f)
            except Exception:
                affected.add(f)

    return PyrightResult(ok=True, diagnostics=diags, affected_files=sorted(affected))

