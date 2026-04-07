"""SaaS: After user agrees, post stored review comments back to GitHub PR."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.github_app import get_installation_token
from app.services import vcs_dispatch
from app.storage.models import GitHubPrBinding, PrReviewReport

logger = logging.getLogger(__name__)


def _parse_comments_from_report(report: PrReviewReport) -> list[dict[str, Any]]:
    if not report.result_json:
        return []
    try:
        data = json.loads(report.result_json)
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


def post_report_comments_to_github(
    session: Session,
    *,
    report: PrReviewReport,
    binding: GitHubPrBinding,
) -> dict[str, Any]:
    """
    Post comments to GitHub using installation token. Marks binding.posted_at.
    Returns {"ok": bool, "posted": int, "skipped": int, "error": optional}
    """
    if binding.posted_at is not None:
        return {"ok": True, "posted": 0, "skipped": 0, "already_posted": True}

    try:
        installation_token = get_installation_token(int(binding.installation_id))
    except Exception as e:
        return {"ok": False, "error": f"get installation token failed: {e}"}

    comments = _parse_comments_from_report(report)
    posted = 0
    skipped = 0

    owner = binding.owner
    repo = binding.repo
    number = str(binding.pr_number)
    head_sha = binding.head_sha or report.head_sha or ""

    for item in comments:
        body = (item.get("suggestion") or item.get("content") or "").strip()
        if not body:
            skipped += 1
            continue
        path = (item.get("file") or "").strip() if isinstance(item.get("file"), str) else ""
        line = item.get("line")
        line_int: Optional[int] = None
        if line is not None and line != "":
            try:
                line_int = int(line) if isinstance(line, (int, float)) else int(str(line).strip())
            except (TypeError, ValueError):
                line_int = None

        res = vcs_dispatch.post_comment(
            "github",
            owner,
            repo,
            number,
            body,
            installation_token,
            path=path if path and path not in ("(整体)", "(未知文件)") else "",
            line=line_int,
            commit_id=head_sha,
            diff=report.result_json or "",
        )
        if res.get("ok"):
            posted += 1
        else:
            # fall back is handled inside github_pr.post_comment; if it still fails, count as skipped
            skipped += 1
            logger.warning("post github comment failed: %s", res.get("error"))

    binding.posted_at = datetime.now(timezone.utc)
    session.add(binding)
    session.commit()
    return {"ok": True, "posted": posted, "skipped": skipped}

