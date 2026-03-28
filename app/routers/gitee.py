"""Gitee API 路由：拉取 PR、下发评论。"""

import json
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import AliasChoices, BaseModel, Field

from app.services import context_enrichment as enrichment_svc
from app.services.api_rate_limit import enforce_vcs_fetch_pr, enforce_vcs_post_comment
from app.services import vcs_dispatch
from app.services import pyright_analyzer as pyright_svc
from app.services import symbol_graph as symbol_graph_svc
from app.services import treesitter_analyzer as treesitter_svc

router = APIRouter(prefix="/api/gitee", tags=["gitee"])


class FetchPRRequest(BaseModel):
    """拉取 PR；token 字段兼容旧字段名 gitee_token。"""

    platform: str = Field("gitee", description="gitee | github")
    pr_url: str
    vcs_token: str = Field(validation_alias=AliasChoices("vcs_token", "gitee_token"))
    enrich_context: bool = False  # 是否拉取测试文件与 import 相关文件并入 file_contexts
    use_symbol_graph: bool = False  # 是否使用 Postgres 增量索引扩展 caller/callee 上下文
    use_treesitter: bool = False  # Tree-sitter: 变更类型识别 + 符号提取
    use_pyright: bool = False  # Pyright: 类型/跨文件诊断（仅 Python，需本地可运行 pyright）


class PostCommentRequest(BaseModel):
    platform: str = Field("gitee", description="gitee | github")
    owner: str
    repo: str
    number: str
    comment: str
    vcs_token: str = Field(validation_alias=AliasChoices("vcs_token", "gitee_token"))
    path: str = ""
    line: Optional[int] = None
    commit_id: str = ""
    diff: str = ""


def json_response(ok: bool, data=None, error: Optional[str] = None):
    return {"ok": ok, "data": data, "error": error}


def run_fetch_pr(req: FetchPRRequest) -> dict:
    """
    执行与 POST /api/gitee/fetch-pr 相同逻辑，供 Webhook 等内部调用。
    返回: {"ok": bool, "data": ..., "error": ...}（error 仅在 ok 为 False 时有效）
    """
    plat = vcs_dispatch.normalize_platform(req.platform)
    result = vcs_dispatch.fetch_pr(plat, req.pr_url, req.vcs_token)
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}
    data = result["data"]
    data["platform"] = plat
    if req.enrich_context and data.get("head_sha"):
        owner = data["owner"]
        repo = data["repo"]
        head_sha = data["head_sha"]
        token = req.vcs_token
        changed = data.get("changed_files") or []

        def fetch_file(path: str):
            return vcs_dispatch.fetch_file_content(plat, owner, repo, path, head_sha, token)

        repo_tree = vcs_dispatch.get_repo_tree_paths(plat, owner, repo, head_sha, token) or None
        data["file_contexts"] = enrichment_svc.enrich_file_contexts(
            data["file_contexts"],
            changed,
            fetch_file,
            add_tests=True,
            add_imports=True,
            repo_tree_paths=repo_tree,
        )
        if req.use_treesitter:
            summary = treesitter_svc.summarize_changes(data.get("diff") or "", data["file_contexts"], changed)
            data["change_kind"] = summary.change_kind
            data["changed_symbols"] = summary.changed_symbols
        if req.use_pyright and (not req.use_treesitter or data.get("change_kind") != "comment_only"):
            pr_res = pyright_svc.run_pyright_in_sandbox(
                owner=owner,
                repo=repo,
                sha=head_sha,
                file_contexts=data["file_contexts"],
                fetch_file=fetch_file,
            )
            # inject summary into contexts (lightweight) and pull affected files if we can
            data["pyright_ok"] = pr_res.ok
            data["pyright_error"] = pr_res.error
            data["pyright_affected_files"] = pr_res.affected_files or []
            if pr_res.ok and pr_res.affected_files:
                for p in (pr_res.affected_files or [])[:15]:
                    if p in data["file_contexts"]:
                        continue
                    c = fetch_file(p)
                    if c:
                        data["file_contexts"][p] = f"[pyright affected]\\n{c}"
            if pr_res.diagnostics:
                # Keep prompt size under control: inject only top N diagnostics summary
                top = pr_res.diagnostics[:30]
                data["file_contexts"]["[pyright diagnostics]"] = json.dumps(top, ensure_ascii=False, indent=2)[:20000]
        if req.use_symbol_graph:
            data["file_contexts"] = symbol_graph_svc.expand_file_contexts_with_symbol_graph(
                owner=owner,
                repo=repo,
                sha=head_sha,
                changed_files=changed,
                file_contexts=data["file_contexts"],
                fetch_file=fetch_file,
            )
    return {"ok": True, "data": data}


@router.post("/fetch-pr")
def fetch_pr(request: Request, req: FetchPRRequest):
    enforce_vcs_fetch_pr(request)
    out = run_fetch_pr(req)
    if not out["ok"]:
        return json_response(False, error=out["error"])
    return json_response(True, data=out["data"])


@router.post("/post-comment")
def post_comment(request: Request, req: PostCommentRequest):
    enforce_vcs_post_comment(request)
    plat = vcs_dispatch.normalize_platform(req.platform)
    result = vcs_dispatch.post_comment(
        plat,
        req.owner,
        req.repo,
        req.number,
        req.comment,
        req.vcs_token,
        path=req.path,
        line=req.line,
        commit_id=req.commit_id,
        diff=req.diff,
    )
    if result["ok"]:
        return json_response(True)
    return json_response(False, error=result["error"])
