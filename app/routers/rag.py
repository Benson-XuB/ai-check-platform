"""RAG 向量文档索引与检索路由。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import AliasChoices, BaseModel, Field

from app.services.api_rate_limit import enforce_rag_index_from_pr
from app.services import vcs_dispatch
from app.services.code_chunker import extract_python_chunks
from app.services import rag_store as rag_svc

router = APIRouter(prefix="/api/rag", tags=["rag"])


class RagIndexDoc(BaseModel):
    content: str
    source_path: Optional[str] = ""
    metadata: Optional[Dict[str, Any]] = None


class RagIndexRequest(BaseModel):
    repo_key: str  # owner/repo or "global"
    source_type: str = "policy"  # readme/wiki/history_bug/...
    embedding_api_key: str
    documents: List[RagIndexDoc]
    chunk_chars: int = 2000
    chunk_overlap_chars: int = 200


class RagSearchRequest(BaseModel):
    repo_key: str
    query_text: str
    embedding_api_key: str
    source_type: Optional[str] = None
    ref: Optional[str] = None
    top_k: int = 5


class RagIndexCodeFromPrRequest(BaseModel):
    platform: str = Field("gitee", description="gitee | github")
    pr_url: str
    vcs_token: str = Field(validation_alias=AliasChoices("vcs_token", "gitee_token"))
    embedding_api_key: str
    prefix: str = ""  # only index changed .py under this prefix
    max_files: int = 80


@router.post("/index")
def index_rag(req: RagIndexRequest):
    if not req.repo_key:
        return {"ok": False, "error": "缺少 repo_key"}
    if not req.embedding_api_key:
        return {"ok": False, "error": "缺少 embedding_api_key"}
    if not req.documents:
        return {"ok": False, "error": "缺少 documents"}
    try:
        indexed = rag_svc.index_rag_documents(
            repo_key=req.repo_key,
            source_type=req.source_type,
            documents=req.documents,
            embedding_api_key=req.embedding_api_key,
            chunk_chars=req.chunk_chars,
            chunk_overlap_chars=req.chunk_overlap_chars,
        )
        return {"ok": True, "data": indexed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/search")
def search_rag(req: RagSearchRequest):
    if not req.repo_key:
        return {"ok": False, "error": "缺少 repo_key"}
    if not req.embedding_api_key:
        return {"ok": False, "error": "缺少 embedding_api_key"}
    if not req.query_text:
        return {"ok": False, "error": "缺少 query_text"}
    try:
        results = rag_svc.search_rag(
            repo_key=req.repo_key,
            query_text=req.query_text,
            embedding_api_key=req.embedding_api_key,
            source_type=req.source_type,
            ref=req.ref,
            top_k=req.top_k,
        )
        return {"ok": True, "data": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/index-code-from-pr")
def index_code_from_pr(request: Request, req: RagIndexCodeFromPrRequest):
    """
    自动索引：从 PR 拉取 head_sha + changed_files，仅对变更的 Python 文件建立 code index（source_type=code）。
    """
    enforce_rag_index_from_pr(request)
    if not req.pr_url:
        return {"ok": False, "error": "缺少 pr_url"}
    if not req.vcs_token:
        return {"ok": False, "error": "缺少 vcs_token / gitee_token"}
    if not req.embedding_api_key:
        return {"ok": False, "error": "缺少 embedding_api_key"}

    plat = vcs_dispatch.normalize_platform(req.platform)
    pr_res = vcs_dispatch.fetch_pr(plat, req.pr_url, req.vcs_token)
    if not pr_res.get("ok"):
        return {"ok": False, "error": pr_res.get("error") or "fetch_pr 失败"}
    data = pr_res["data"]
    owner = data.get("owner") or ""
    repo = data.get("repo") or ""
    head_sha = data.get("head_sha") or ""
    if not owner or not repo or not head_sha:
        return {"ok": False, "error": "PR 信息不完整（缺 owner/repo/head_sha）"}
    repo_key = f"{owner}/{repo}"

    changed = data.get("changed_files") or []
    py_paths = [p for p in changed if isinstance(p, str) and p.endswith(".py")]
    if req.prefix:
        px = req.prefix.rstrip("/") + "/"
        py_paths = [p for p in py_paths if p.startswith(px)]
    py_paths = py_paths[: max(1, min(int(req.max_files or 80), 300))]

    documents: List[Dict[str, Any]] = []
    for p in py_paths:
        raw = vcs_dispatch.fetch_file_content(plat, owner, repo, p, head_sha, req.vcs_token)
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
                        "pr_url": req.pr_url,
                    },
                }
            )

    try:
        indexed = rag_svc.index_rag_documents(
            repo_key=repo_key,
            source_type="code",
            documents=documents,
            embedding_api_key=req.embedding_api_key,
            chunk_chars=20000,
            chunk_overlap_chars=0,
        )
        return {
            "ok": True,
            "data": {
                "repo_key": repo_key,
                "head_sha": head_sha,
                "indexed": indexed,
                "changed_py_files": len(py_paths),
                "chunks_prepared": len(documents),
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

