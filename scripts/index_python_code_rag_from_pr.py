#!/usr/bin/env python3
"""
从 PR 链接拉取 head_sha + changed_files，并仅对变更相关的 Python 文件建立 code 索引（source_type=code）。

用法：
  export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db"
  python scripts/index_python_code_rag_from_pr.py \
    --pr-url https://gitee.com/owner/repo/pulls/123 \
    --gitee-token xxx \
    --embedding-key xxx

说明：
- 仅索引 PR 变更文件中的 .py
- 每个 chunk 的 meta 中写入 ref=head_sha，供检索时精确过滤
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import gitee as gitee_svc
from app.services import rag_store as rag_svc
from scripts.index_python_code_rag_from_gitee import extract_python_chunks  # reuse


def main() -> None:
    parser = argparse.ArgumentParser(description="Index changed python files from PR into pgvector (source_type=code)")
    parser.add_argument("--pr-url", required=True, help="Gitee PR 链接")
    parser.add_argument("--gitee-token", required=True, help="Gitee token")
    parser.add_argument("--embedding-key", required=True, help="DashScope embedding api key")
    parser.add_argument("--max-files", type=int, default=80, help="max changed .py files to index")
    args = parser.parse_args()

    pr_res = gitee_svc.fetch_pr(args.pr_url, args.gitee_token)
    if not pr_res.get("ok"):
        raise SystemExit(f"fetch_pr failed: {pr_res.get('error')}")
    data = pr_res["data"]
    owner = data.get("owner")
    repo = data.get("repo")
    head_sha = data.get("head_sha") or ""
    if not owner or not repo or not head_sha:
        raise SystemExit("missing owner/repo/head_sha from PR fetch")
    repo_key = f"{owner}/{repo}"

    changed = data.get("changed_files") or []
    py_paths = [p for p in changed if isinstance(p, str) and p.endswith(".py")]
    py_paths = py_paths[: args.max_files]

    documents: List[Dict] = []
    indexed = 0

    for p in py_paths:
        raw = gitee_svc.fetch_file_content(owner, repo, p, head_sha, args.gitee_token)
        if not raw:
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
                        "ref": head_sha,
                        "pr_url": args.pr_url,
                    },
                }
            )

    if documents:
        res = rag_svc.index_rag_documents(
            repo_key=repo_key,
            source_type="code",
            documents=documents,
            embedding_api_key=args.embedding_key,
            chunk_chars=20000,
            chunk_overlap_chars=0,
        )
        indexed += int(res.get("indexed_chunks") or 0)

    print(f"repo_key={repo_key} head_sha={head_sha}")
    print(f"indexed_code_chunks={indexed} from_py_files={len(py_paths)}")


if __name__ == "__main__":
    main()

