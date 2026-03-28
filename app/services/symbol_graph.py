"""
Incremental Symbol Graph (PostgreSQL-backed, best-effort).

Goal (v1):
- Parse already-fetched Python files (from file_contexts) to extract:
  - symbol definitions (function/class)
  - call edges (callee name only)
- Upsert/insert these into Postgres
- Given changed symbols, query callers and fetch those files into file_contexts

This is intentionally approximate; it improves over time as more files are indexed.
"""

from __future__ import annotations

import ast
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.storage.db import create_db_engine
from app.storage.models import SymbolCallEdge, SymbolDefinition


def _get_engine() -> Optional[Engine]:
    return create_db_engine()


def extract_python_defs_and_calls(content: str) -> Tuple[List[Tuple[str, str, int]], List[Tuple[str, int]]]:
    """
    Returns:
      defs: [(symbol, kind, line)]
      calls: [(callee, line)]
    """
    defs: List[Tuple[str, str, int]] = []
    calls: List[Tuple[str, int]] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return defs, calls

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append((node.name, "function", getattr(node, "lineno", 0) or 0))
        elif isinstance(node, ast.ClassDef):
            defs.append((node.name, "class", getattr(node, "lineno", 0) or 0))
        elif isinstance(node, ast.Call):
            callee = None
            fn = node.func
            if isinstance(fn, ast.Name):
                callee = fn.id
            elif isinstance(fn, ast.Attribute):
                # obj.method(...) -> method (best-effort)
                callee = fn.attr
            if callee:
                calls.append((callee, getattr(node, "lineno", 0) or 0))

    # de-dup but keep stable order-ish
    seen_defs: Set[Tuple[str, str, int]] = set()
    dedup_defs: List[Tuple[str, str, int]] = []
    for d in defs:
        if d in seen_defs:
            continue
        seen_defs.add(d)
        dedup_defs.append(d)

    seen_calls: Set[Tuple[str, int]] = set()
    dedup_calls: List[Tuple[str, int]] = []
    for c in calls:
        if c in seen_calls:
            continue
        seen_calls.add(c)
        dedup_calls.append(c)

    return dedup_defs, dedup_calls


def _filter_indexable_python_files(file_contexts: Dict[str, str]) -> List[Tuple[str, str]]:
    """
    Only index real repo files:
    - endswith .py
    - skip synthetic keys like "[语义检索-1] ..." or "[import 相关] ..."
    """
    out: List[Tuple[str, str]] = []
    for path, content in file_contexts.items():
        if not path.endswith(".py"):
            continue
        if path.startswith("["):
            continue
        if not content:
            continue
        out.append((path, content))
    return out


def _guess_changed_symbols(changed_files: Iterable[str], file_contexts: Dict[str, str]) -> List[str]:
    """
    Best-effort: use definitions present in changed files as the query symbols.
    """
    symbols: List[str] = []
    for path in changed_files:
        if not path or not path.endswith(".py"):
            continue
        content = file_contexts.get(path)
        if not content:
            continue
        defs, _ = extract_python_defs_and_calls(content)
        for sym, _, _line in defs:
            symbols.append(sym)
    # de-dup preserve order
    seen: Set[str] = set()
    out: List[str] = []
    for s in symbols:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out[:30]


def index_file_contexts(repo_key: str, sha: str, file_contexts: Dict[str, str]) -> None:
    """
    Incrementally index python defs/calls from the provided file_contexts.
    Overwrites prior records for the same (repo_key, sha, path) to avoid duplication.
    """
    engine = _get_engine()
    if engine is None:
        return
    files = _filter_indexable_python_files(file_contexts)
    if not files:
        return

    with Session(engine) as session:
        for path, content in files:
            defs, calls = extract_python_defs_and_calls(content)

            # Clear existing records for this file@sha
            session.execute(
                delete(SymbolDefinition).where(
                    SymbolDefinition.repo_key == repo_key,
                    SymbolDefinition.sha == sha,
                    SymbolDefinition.path == path,
                )
            )
            session.execute(
                delete(SymbolCallEdge).where(
                    SymbolCallEdge.repo_key == repo_key,
                    SymbolCallEdge.sha == sha,
                    SymbolCallEdge.from_path == path,
                )
            )

            for sym, kind, line in defs:
                session.add(
                    SymbolDefinition(
                        repo_key=repo_key,
                        sha=sha,
                        path=path,
                        symbol=sym,
                        kind=kind,
                        line=line,
                    )
                )

            for callee, line in calls:
                session.add(
                    SymbolCallEdge(
                        repo_key=repo_key,
                        sha=sha,
                        from_path=path,
                        callee=callee,
                        line=line,
                    )
                )
        session.commit()


def find_callers(repo_key: str, sha: str, symbols: List[str], limit: int = 25) -> List[str]:
    """
    Return file paths that call any of the given symbols.
    """
    engine = _get_engine()
    if engine is None or not symbols:
        return []
    with Session(engine) as session:
        stmt = (
            select(SymbolCallEdge.from_path)
            .where(SymbolCallEdge.repo_key == repo_key, SymbolCallEdge.sha == sha, SymbolCallEdge.callee.in_(symbols))
            .distinct()
            .limit(limit)
        )
        rows = session.execute(stmt).all()
    return [r[0] for r in rows if r and r[0]]


def expand_file_contexts_with_symbol_graph(
    *,
    owner: str,
    repo: str,
    sha: str,
    changed_files: List[str],
    file_contexts: Dict[str, str],
    fetch_file: Callable[[str], Optional[str]],
    max_extra_files: int = 10,
) -> Dict[str, str]:
    """
    1) Index current file_contexts into Postgres (incremental)
    2) Guess changed symbols from changed_files
    3) Query callers from symbol graph, fetch those files, and merge into file_contexts

    If DATABASE_URL is not configured, returns input unchanged.
    """
    repo_key = f"{owner}/{repo}"
    engine = _get_engine()
    if engine is None:
        return dict(file_contexts)

    # Index what we currently know
    index_file_contexts(repo_key, sha, file_contexts)

    # Find symbols to expand from
    symbols = _guess_changed_symbols(changed_files, file_contexts)
    if not symbols:
        return dict(file_contexts)

    callers = find_callers(repo_key, sha, symbols, limit=50)
    merged = dict(file_contexts)
    added = 0
    for p in callers:
        if added >= max_extra_files:
            break
        if not p or p in merged:
            continue
        content = fetch_file(p)
        if content:
            merged[p] = f"[symbol-graph caller]\\n{content}"
            added += 1
    return merged

