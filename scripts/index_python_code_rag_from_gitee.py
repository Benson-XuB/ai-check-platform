#!/usr/bin/env python3
"""
把 Gitee 仓库的 Python 代码按“函数/类”切 chunk，并写入 pgvector（source_type=code）。

用法：
  export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db"
  python scripts/index_python_code_rag_from_gitee.py \
    --repo-key owner/repo \
    --gitee-token xxx \
    --embedding-key xxx \
    --ref master \
    --prefix backend/app

说明：
- 仅索引 .py 文件
- chunk 粒度：ClassDef / FunctionDef（含方法）
- meta 中写入：language/symbol/kind/start_line/end_line/ref
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import gitee as gitee_svc
from app.services import rag_store as rag_svc


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


def extract_python_chunks(path: str, content: str) -> List[CodeChunk]:
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
            # 控制单 chunk 大小，避免 embedding 过长
            if len(snippet) > 8000:
                snippet = snippet[:8000]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Index Python code chunks to pgvector (source_type=code)")
    parser.add_argument("--repo-key", required=True, help="owner/repo")
    parser.add_argument("--gitee-token", required=True, help="Gitee token")
    parser.add_argument("--embedding-key", required=True, help="DashScope embedding api key")
    parser.add_argument("--ref", default="master", help="branch or sha (建议传 head_sha 以避免过期索引)")
    parser.add_argument("--prefix", default="", help="only index paths under this prefix (e.g. backend/app)")
    parser.add_argument("--max-files", type=int, default=300, help="max .py files to index")
    args = parser.parse_args()

    owner, repo = args.repo_key.split("/", 1)
    paths = gitee_svc.get_repo_tree_paths(owner, repo, args.ref, args.gitee_token)
    if args.prefix:
        paths = [p for p in paths if p.startswith(args.prefix.rstrip("/") + "/")]

    py_paths = [p for p in paths if p.endswith(".py")]
    py_paths = py_paths[: args.max_files]

    documents: List[Dict] = []
    indexed_chunks = 0

    for p in py_paths:
        raw = gitee_svc.fetch_file_content(owner, repo, p, args.ref, args.gitee_token)
        if not raw or len(raw) > 50000:
            continue
        for ch in extract_python_chunks(p, raw):
            documents.append(
                {
                    "content": ch.content,
                    "source_path": f"{ch.path}:{ch.start_line}-{ch.end_line}",
                    "metadata": {
                        "language": "python",
                        "symbol": ch.symbol,
                        "kind": ch.kind,
                        "start_line": ch.start_line,
                        "end_line": ch.end_line,
                        "ref": args.ref,
                    },
                }
            )
        # 批量写入，避免单次 documents 过大
        if len(documents) >= 200:
            res = rag_svc.index_rag_documents(
                repo_key=args.repo_key,
                source_type="code",
                documents=documents,
                embedding_api_key=args.embedding_key,
                chunk_chars=20000,  # 避免二次切块
                chunk_overlap_chars=0,
            )
            indexed_chunks += int(res.get("indexed_chunks") or 0)
            documents = []

    if documents:
        res = rag_svc.index_rag_documents(
            repo_key=args.repo_key,
            source_type="code",
            documents=documents,
            embedding_api_key=args.embedding_key,
            chunk_chars=20000,
            chunk_overlap_chars=0,
        )
        indexed_chunks += int(res.get("indexed_chunks") or 0)

    print(f"Indexed code chunks: {indexed_chunks}")


if __name__ == "__main__":
    main()

