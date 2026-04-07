"""GitHub SaaS：处理 GitHub App Webhook，写入报告并更新 Check Run。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.routers.gitee import FetchPRRequest, run_fetch_pr
from app.routers.review import ReviewRequest, run_review_core
from app.services.github_app import get_installation_token
from app.services.github_checks import complete_check_run, create_check_run
from app.services.gitee_saas import platform_llm_key
from app.storage.db import create_db_engine
from app.storage.models import GitHubPrBinding, PrReviewReport

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _report_details_url(report_id: int) -> str:
    # We reuse the /app page as the main UI entry; the UI can fetch /api/saas/reports/{id}.
    return f"{_public_base_url()}/app#report-{int(report_id)}"


def should_handle_github_pull_request_event(event: str, payload: dict[str, Any]) -> bool:
    if (event or "").strip() != "pull_request":
        return False
    action = (payload.get("action") or "").strip()
    return action in ("opened", "reopened", "synchronize")


def process_saas_github_pull_request_webhook(
    payload: dict[str, Any],
    *,
    app_user_id: int,
    installation_id: int,
) -> None:
    """
    Background: fetch PR -> run review -> save report -> update Check Run.
    Detailed comments are NOT posted here. They are posted after user agrees.
    """
    engine = create_db_engine()
    if not engine:
        logger.error("SaaS GitHub needs DATABASE_URL")
        return

    repo = payload.get("repository") or {}
    full_name = repo.get("full_name") or ""
    if not isinstance(full_name, str) or "/" not in full_name:
        logger.error("GitHub webhook missing repository.full_name")
        return
    owner, repo_name = full_name.split("/", 1)

    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    try:
        pr_number = int(number) if number is not None else 0
    except (TypeError, ValueError):
        pr_number = 0
    head_sha = ""
    head = pr.get("head") or {}
    if isinstance(head, dict):
        head_sha = (head.get("sha") or "") if isinstance(head.get("sha"), str) else ""
    if not head_sha:
        head_sha = (pr.get("head_sha") or "") if isinstance(pr.get("head_sha"), str) else ""

    pr_url = pr.get("html_url") or f"https://github.com/{owner}/{repo_name}/pull/{pr_number}"

    provider, llm_key = platform_llm_key()
    if not llm_key:
        _save_report_failed(engine, app_user_id, f"{owner}/{repo_name}", pr_number, head_sha or None, pr.get("title"), "平台未配置 LLM API Key")
        return

    try:
        installation_token = get_installation_token(int(installation_id))
    except Exception as e:
        _save_report_failed(engine, app_user_id, f"{owner}/{repo_name}", pr_number, head_sha or None, pr.get("title"), f"获取 GitHub installation token 失败: {e}")
        return

    check_run_id: Optional[int] = None
    try:
        if head_sha:
            # Create initial in-progress check run (details_url will be updated after report is saved).
            check_run_id = create_check_run(
                installation_token=installation_token,
                owner=owner,
                repo=repo_name,
                head_sha=head_sha,
                name="AI Review",
                details_url=f"{_public_base_url()}/app",
                summary="AI review is running. Open SaaS to see details.",
            )
    except Exception:
        # Non-fatal; continue review even if checks API fails.
        logger.exception("create_check_run failed")

    fetch_req = FetchPRRequest(
        platform="github",
        pr_url=str(pr_url),
        vcs_token=installation_token,
        enrich_context=_env_bool("SAAS_WEBHOOK_ENRICH_CONTEXT", default=False),
        use_symbol_graph=_env_bool("SAAS_WEBHOOK_USE_SYMBOL_GRAPH", default=False),
        use_treesitter=_env_bool("SAAS_WEBHOOK_USE_TREESITTER", default=False),
        use_pyright=_env_bool("SAAS_WEBHOOK_USE_PYRIGHT", default=False),
    )
    out = run_fetch_pr(fetch_req)
    if not out.get("ok"):
        err = str(out.get("error") or "fetch failed")
        report_id = _save_report_failed(engine, app_user_id, f"{owner}/{repo_name}", pr_number, head_sha or None, pr.get("title"), err)
        _maybe_complete_check(engine, installation_token, owner, repo_name, check_run_id, report_id, ok=False, summary=err)
        return

    data = out["data"]

    use_default = provider == "dashscope" and _env_bool("SAAS_WEBHOOK_USE_DEFAULT_REVIEW", default=True)
    try:
        default_passes = int(os.getenv("SAAS_WEBHOOK_DEFAULT_PASSES", "8"))
    except ValueError:
        default_passes = 8

    review_req = ReviewRequest(
        diff=data.get("diff") or "",
        pr_title=data.get("title") or "",
        pr_body=data.get("body") or "",
        file_contexts=data.get("file_contexts") or {},
        llm_provider=provider,
        llm_api_key=llm_key,
        use_mock=False,
        use_default_review=use_default,
        default_passes=default_passes,
        use_semantic_context=_env_bool("SAAS_WEBHOOK_USE_SEMANTIC_CONTEXT", default=False),
        repo_key=f'{data.get("owner")}/{data.get("repo")}' if data.get("owner") and data.get("repo") else None,
        ref=data.get("head_sha") or None,
    )
    review_out = run_review_core(review_req)
    if not review_out.get("ok"):
        err = str(review_out.get("error") or "review failed")
        report_id = _save_report_failed(engine, app_user_id, f"{owner}/{repo_name}", pr_number, head_sha or None, pr.get("title"), err)
        _maybe_complete_check(engine, installation_token, owner, repo_name, check_run_id, report_id, ok=False, summary=err)
        return

    payload_json = json.dumps(review_out.get("data") or {}, ensure_ascii=False)
    report_id = _save_report_ok(engine, app_user_id, f"{owner}/{repo_name}", pr_number, head_sha or None, pr.get("title"), payload_json)
    _save_binding(engine, report_id, app_user_id, int(installation_id), owner, repo_name, pr_number, head_sha or None, check_run_id)

    # Update check run with report link
    summary = "AI review ready. Open details to view report. Click Agree in SaaS to post comments to PR."
    _maybe_complete_check(engine, installation_token, owner, repo_name, check_run_id, report_id, ok=True, summary=summary)


def _save_report_ok(engine, user_id: int, path_ns: str, pr_number: int, head_sha: Optional[str], pr_title: Any, result_json: str) -> int:
    with Session(engine) as session:
        row = PrReviewReport(
            user_id=user_id,
            path_with_namespace=path_ns,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=str(pr_title)[:1024] if isinstance(pr_title, str) else None,
            status="completed",
            result_json=result_json,
            error=None,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def _save_report_failed(engine, user_id: int, path_ns: str, pr_number: int, head_sha: Optional[str], pr_title: Any, err: str) -> int:
    with Session(engine) as session:
        row = PrReviewReport(
            user_id=user_id,
            path_with_namespace=path_ns,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=str(pr_title)[:1024] if isinstance(pr_title, str) else None,
            status="failed",
            result_json=None,
            error=(err or "")[:8000],
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def _save_binding(
    engine,
    report_id: int,
    user_id: int,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: Optional[str],
    check_run_id: Optional[int],
) -> None:
    with Session(engine) as session:
        row = GitHubPrBinding(
            report_id=report_id,
            user_id=user_id,
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            check_run_id=check_run_id,
        )
        session.add(row)
        session.commit()


def _maybe_complete_check(
    engine,
    installation_token: str,
    owner: str,
    repo: str,
    check_run_id: Optional[int],
    report_id: int,
    *,
    ok: bool,
    summary: str,
) -> None:
    if not check_run_id:
        return
    try:
        complete_check_run(
            installation_token=installation_token,
            owner=owner,
            repo=repo,
            check_run_id=int(check_run_id),
            details_url=_report_details_url(report_id),
            conclusion="success" if ok else "failure",
            summary=summary,
            title="AI Review",
        )
    except Exception:
        logger.exception("complete_check_run failed")

