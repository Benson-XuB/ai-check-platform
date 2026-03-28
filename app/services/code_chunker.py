"""
Code chunker utilities for building code embeddings index.

Current scope:
- Python: chunk by top-level Function/Class definitions (and nested via ast.walk),
  storing line range metadata for precise retrieval.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CodeChunk:
    path: str
    symbol: str
    kind: str  # function | class
    start_line: int
    end_line: int
    content: str


def _safe_get_end_lineno(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    return int(end) if isinstance(end, int) and end > 0 else int(getattr(node, "lineno", 1) or 1)


def extract_python_chunks(path: str, content: str, *, max_chunk_chars: int = 8000) -> List[CodeChunk]:
    content = content or ""
    if not content.strip():
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    lines = content.splitlines()
    chunks: List[CodeChunk] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = int(getattr(node, "lineno", 1) or 1)
            end = _safe_get_end_lineno(node)
            start = max(1, start)
            end = max(start, min(end, len(lines)))
            symbol = getattr(node, "name", "") or ""
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            snippet = "\n".join(lines[start - 1 : end]).strip()
            if not snippet:
                continue
            if len(snippet) > max_chunk_chars:
                snippet = snippet[:max_chunk_chars]
            chunks.append(
                CodeChunk(
                    path=path,
                    symbol=symbol,
                    kind=kind,
                    start_line=start,
                    end_line=end,
                    content=snippet,
                )
            )
    return chunks

