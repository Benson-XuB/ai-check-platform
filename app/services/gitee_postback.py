"""SaaS：用户在报告页「同意」单条意见后，用 Gitee OAuth token 回写到 MR。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.routers.gitee import FetchPRRequest, run_fetch_pr
from app.services import gitee as gitee_svc
from app.services.gitee_saas import split_path_with_namespace
from app.storage.models import GiteePostedComment, PrReviewReport

logger = logging.getLogger(__name__)


def gitee_comment_item_key(item: dict[str, Any]) -> str:
    file = str(item.get("file") or "")
    line = str(item.get("line") or "")
    sev = str(item.get("severity") or "")
    cat = str(item.get("category") or "")
    body = str((item.get("suggestion") or item.get("content") or "")).strip()
    raw = "\n".join([file, line, sev, cat, body])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:64]


def parse_comments_from_report_json(result_json: Optional[str]) -> list[dict[str, Any]]:
    if not result_json:
        return []
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    comments = data.get("comments")
    if not isinstance(comments, list):
        return []
    out: list[dict[str, Any]] = []
    for item in comments:
        if isinstance(item, dict):
            out.append(item)
    return out


def posted_item_keys_for_report(session: Session, report_id: int) -> set[str]:
    rows = session.scalars(
        select(GiteePostedComment.item_key).where(GiteePostedComment.report_id == report_id)
    ).all()
    return {str(k) for k in rows if k}


def post_one_gitee_report_comment(
    session: Session,
    *,
    report: PrReviewReport,
    comment_index: int,
    gitee_access_token: str,
) -> dict[str, Any]:
    """
    将报告中第 comment_index 条意见发到 Gitee MR（行级评论，失败时回落为正文）。
    成功则写入 GiteePostedComment 防重复。
    """
    comments = parse_comments_from_report_json(report.result_json)
    if comment_index < 0 or comment_index >= len(comments):
        return {"ok": False, "error": "无效的 comment_index"}
    item = comments[comment_index]
    ik = gitee_comment_item_key(item)
    exists = session.scalars(
        select(GiteePostedComment).where(
            GiteePostedComment.report_id == report.id,
            GiteePostedComment.item_key == ik,
        )
    ).first()
    if exists:
        return {"ok": True, "posted": 0, "skipped": 1, "already": True}

    body = (item.get("suggestion") or item.get("content") or "").strip()
    if not body:
        return {"ok": False, "error": "该条意见内容为空"}

    path_raw = item.get("file")
    path = (path_raw.strip() if isinstance(path_raw, str) else "") or ""
    if path in ("(整体)", "(未知文件)"):
        path = ""
    line_int: Optional[int] = None
    line = item.get("line")
    if line is not None and line != "":
        try:
            line_int = int(line) if isinstance(line, (int, float)) else int(str(line).strip())
        except (TypeError, ValueError):
            line_int = None

    pr_url = f"https://gitee.com/{report.path_with_namespace.strip()}/pulls/{int(report.pr_number)}"
    fetch_out = run_fetch_pr(
        FetchPRRequest(
            platform="gitee",
            pr_url=pr_url,
            vcs_token=gitee_access_token,
            enrich_context=False,
            use_symbol_graph=False,
            use_treesitter=False,
            use_pyright=False,
        )
    )
    if not fetch_out.get("ok"):
        return {"ok": False, "error": str(fetch_out.get("error") or "拉取 PR 失败")}
    data = fetch_out["data"]
    diff = data.get("diff") or ""
    head_sha = (data.get("head_sha") or report.head_sha or "")[:80]

    try:
        owner, repo = split_path_with_namespace(report.path_with_namespace)
    except ValueError:
        return {"ok": False, "error": "无效的仓库路径"}

    res = gitee_svc.post_comment(
        owner,
        repo,
        str(int(report.pr_number)),
        body,
        gitee_access_token,
        path=path,
        line=line_int,
        commit_id=head_sha,
        diff=diff,
    )
    if not res.get("ok"):
        logger.warning("gitee post comment failed report=%s idx=%s: %s", report.id, comment_index, res.get("error"))
        return {"ok": False, "error": str(res.get("error") or "发帖失败")}

    session.add(GiteePostedComment(report_id=report.id, item_key=ik))
    session.commit()
    return {"ok": True, "posted": 1, "skipped": 0, "already": False}
