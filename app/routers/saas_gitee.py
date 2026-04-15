"""Gitee OAuth 登录、同步 WebHook、审查报告列表。"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.gitee_saas import (
    exchange_code_for_token,
    fetch_gitee_user,
    list_repo_open_pulls,
    list_user_repos,
    oauth_config_ok,
    run_saas_gitee_onboarding_review_for_url,
    sync_hooks_for_user,
    upsert_user_from_gitee_token,
)
from app.services.oauth_state import make_signed_oauth_state, verify_signed_oauth_state
from app.storage.db import create_db_engine
from app.storage.models import AppUser, GiteeOAuthAccount, GiteeWatchedRepo, PrReviewReport

logger = logging.getLogger(__name__)

router = APIRouter(tags=["saas-gitee"])

def _saas_enable_gitee() -> bool:
    """
    Gitee SaaS routes are enabled by default.

    Turn off explicitly (e.g. GitHub-only deploy or bad egress to gitee.com):
    - SAAS_DISABLE_GITEE=1|true|yes|on
    - SAAS_ENABLE_GITEE=0|false|no|off

    If both are set, SAAS_DISABLE_GITEE wins.
    """
    if (os.getenv("SAAS_DISABLE_GITEE") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    raw = (os.getenv("SAAS_ENABLE_GITEE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


_GITEE_SAAS_DISABLED_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gitee SaaS 已关闭</title>
<style>
body{font-family:system-ui,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1c1917}
code{background:#f5f5f4;padding:0.1em 0.35em;border-radius:4px;font-size:0.9em}
a{color:#134e4a;font-weight:600}
</style></head><body>
<h1>此服务上 Gitee 登录未开启</h1>
<p>当前进程判定 Gitee SaaS 为<strong>关闭</strong>。若你已在平台里设置了 <code>SAAS_ENABLE_GITEE=1</code> 仍看到本页，多半是<strong>环境变量加在了别的服务上</strong>，或 Compose/镜像里写死了 <code>SAAS_ENABLE_GITEE=0</code>。</p>
<p>关闭方式（任选其一）：<code>SAAS_DISABLE_GITEE=1</code>，或 <code>SAAS_ENABLE_GITEE=0</code>。默认<strong>不配置则为开启</strong>。</p>
<p><a href="/">返回首页</a> · <a href="/manual">手动审查</a> · <a href="/auth/github/install">GitHub App</a></p>
</body></html>"""

_GITEE_OAUTH_SETUP_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>需要配置 Gitee OAuth</title>
<style>
body{font-family:system-ui,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1c1917}
code{background:#f5f5f4;padding:0.1em 0.35em;border-radius:4px;font-size:0.9em}
pre{background:#f5f5f4;padding:1rem;border-radius:8px;overflow:auto;font-size:0.85rem}
a{color:#134e4a;font-weight:600}
</style></head><body>
<h1>尚未配置 Gitee 登录</h1>
<p>服务端缺少 OAuth 环境变量，无法跳转到 Gitee。请在项目根目录创建 <code>.env</code>（可参考 <code>.env.example</code>），并填写：</p>
<ul>
<li><code>GITEE_OAUTH_CLIENT_ID</code></li>
<li><code>GITEE_OAUTH_CLIENT_SECRET</code></li>
<li><code>GITEE_OAUTH_REDIRECT_URI</code>（须与 Gitee 第三方应用里的「回调地址」完全一致，例如 <code>http://127.0.0.1:8000/auth/gitee/callback</code>）</li>
</ul>
<p>同时需要 <code>DATABASE_URL</code>、<code>SESSION_SECRET</code>。修改后请<strong>重启</strong> <code>uvicorn</code>。</p>
<p>应用启动时会加载 <strong>仓库根目录</strong>的 <code>.env</code>，以及 <code>ai-check-platform/.env</code>（后者可覆盖前者）。请确认变量名无拼写错误、值不为空，修改后<strong>重启</strong> <code>uvicorn</code>。</p>
<p><a href="/">返回首页</a> · <a href="/manual">手动审查（无需 OAuth）</a></p>
</body></html>"""

_DATABASE_SETUP_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>需要配置数据库</title>
<style>
body{font-family:system-ui,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1c1917}
code{background:#f5f5f4;padding:0.1em 0.35em;border-radius:4px}
a{color:#134e4a;font-weight:600}
</style></head><body>
<h1>需要 DATABASE_URL</h1>
<p>Gitee 登录与报告存储需要数据库。请在 <code>.env</code> 中设置 <code>DATABASE_URL</code>（例如 PostgreSQL），然后重启服务。</p>
<p><a href="/">返回首页</a> · <a href="/manual">手动审查</a></p>
</body></html>"""


def _require_db() -> None:
    if not create_db_engine():
        raise HTTPException(
            status_code=503,
            detail="SaaS 功能需要配置 DATABASE_URL（PostgreSQL 等）",
        )


def _session_user_id(request: Request) -> Optional[int]:
    raw = request.session.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.get("/auth/gitee/login")
def gitee_oauth_login(request: Request):
    if not _saas_enable_gitee():
        return HTMLResponse(content=_GITEE_SAAS_DISABLED_HTML, status_code=403)
    if not oauth_config_ok():
        return HTMLResponse(content=_GITEE_OAUTH_SETUP_HTML, status_code=503)
    if not create_db_engine():
        return HTMLResponse(content=_DATABASE_SETUP_HTML, status_code=503)
    # 登录成功后的落地页（默认须为 Gitee 控制台，勿用 /app 以免与 GitHub 混用）
    request.session["post_login_redirect"] = "/app-gitee"
    state = make_signed_oauth_state()
    from app.services.gitee_saas import gitee_oauth_authorize_url

    return RedirectResponse(url=gitee_oauth_authorize_url(state), status_code=302)


@router.get("/auth/gitee/callback")
def gitee_oauth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None):
    if not _saas_enable_gitee():
        return HTMLResponse(content=_GITEE_SAAS_DISABLED_HTML, status_code=403)
    if not code:
        raise HTTPException(400, "缺少 code")
    if not verify_signed_oauth_state(state):
        raise HTTPException(
            400,
            "OAuth state 无效（请从本站「使用 Gitee 登录」重新发起；勿复制回调链接；"
            "并保证 Gitee 回调地址与访问站点同为 www 或同为非 www）",
        )
    _require_db()
    try:
        token_json = exchange_code_for_token(code)
        gu = fetch_gitee_user(token_json["access_token"])
        user = upsert_user_from_gitee_token(token_json, gu)
    except Exception as e:
        logger.exception("Gitee OAuth callback failed")
        raise HTTPException(400, str(e)) from e
    request.session["user_id"] = user.id
    next_url = request.session.pop("post_login_redirect", "/app-gitee")
    return RedirectResponse(url=next_url, status_code=302)


@router.post("/auth/logout")
def logout(request: Request):
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    request.session.clear()
    return {"ok": True}


@router.get("/api/saas/gitee/me")
def saas_me(request: Request):
    if not _saas_enable_gitee():
        # keep consistent with unauthenticated shape so UI doesn't explode
        return {"ok": True, "authenticated": False, "disabled": True, "provider": "gitee"}
    uid = _session_user_id(request)
    if not uid:
        return {"ok": True, "authenticated": False}
    engine = create_db_engine()
    if not engine:
        return {"ok": False, "error": "no database"}
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        acc = session.scalars(select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == uid)).first()
        if not user or not acc:
            request.session.clear()
            return {"ok": True, "authenticated": False}
        watched = session.scalars(
            select(GiteeWatchedRepo).where(GiteeWatchedRepo.user_id == uid)
        ).all()
        return {
            "ok": True,
            "authenticated": True,
            "user": {
                "id": user.id,
                "gitee_login": acc.login,
                "saas_webhook_token": user.saas_webhook_token,
                "watched_repos": len(watched),
            },
        }


@router.post("/api/saas/gitee/sync-hooks")
def saas_sync_hooks(request: Request) -> dict[str, Any]:
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    return sync_hooks_for_user(uid)


@router.get("/api/saas/gitee/reports")
def saas_reports(request: Request, limit: int = 50, offset: int = 0):
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with Session(engine) as session:
        total = (
            session.scalar(
                select(func.count())
                .select_from(PrReviewReport)
                .where(PrReviewReport.user_id == uid)
            )
            or 0
        )
        stmt = (
            select(PrReviewReport)
            .where(PrReviewReport.user_id == uid)
            .order_by(PrReviewReport.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = session.scalars(stmt).all()
        return {
            "ok": True,
            "data": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [
                    {
                        "id": r.id,
                        "path_with_namespace": r.path_with_namespace,
                        "pr_number": r.pr_number,
                        "head_sha": r.head_sha,
                        "pr_title": r.pr_title,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
            },
        }


@router.get("/api/saas/gitee/reports/{report_id}")
def saas_report_detail(request: Request, report_id: int):
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    with Session(engine) as session:
        r = session.get(PrReviewReport, report_id)
        if not r or r.user_id != uid:
            raise HTTPException(404, "报告不存在")
        parsed = None
        if r.result_json:
            try:
                parsed = json.loads(r.result_json)
            except json.JSONDecodeError:
                parsed = None
        return {
            "ok": True,
            "data": {
                "id": r.id,
                "path_with_namespace": r.path_with_namespace,
                "pr_number": r.pr_number,
                "head_sha": r.head_sha,
                "pr_title": r.pr_title,
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "result": parsed,
            },
        }


class GiteeOnboardingReviewBody(BaseModel):
    """用户勾选的 Gitee 合并请求链接；仅手动触发审查，与 WebHook 无关。"""

    pr_urls: list[str] = Field(..., min_length=1, max_length=10)


@router.get("/api/saas/gitee/onboarding/repos")
def gitee_onboarding_repos(request: Request, page: int = 1, per_page: int = 50):
    """当前用户 Gitee 账号下有权限的仓库（分页），用于选择仓库后列出 open 合并请求。"""
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    with Session(engine) as session:
        acc = session.scalars(select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == uid)).first()
        if not acc:
            raise HTTPException(404, "未绑定 Gitee 账号")
        token = acc.access_token
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    raw = list_user_repos(token, page=page, per_page=per_page)
    items = []
    for r in raw:
        pns = r.get("path_with_namespace") or r.get("full_name")
        if not pns:
            continue
        items.append(
            {
                "path_with_namespace": str(pns).strip(),
                "name": str(r.get("name") or ""),
                "description": str(r.get("description") or "")[:240],
            }
        )
    return {
        "ok": True,
        "data": {
            "items": items,
            "page": page,
            "per_page": per_page,
            "has_more": len(raw) >= per_page,
        },
    }


@router.get("/api/saas/gitee/onboarding/open-merge-requests")
def gitee_onboarding_open_merge_requests(
    request: Request,
    path_with_namespace: str,
    page: int = 1,
    per_page: int = 50,
):
    """指定仓库下 state=open 的合并请求（仅列表，不审查）。"""
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    pns = (path_with_namespace or "").strip()
    if not pns or "/" not in pns:
        raise HTTPException(400, "无效的 path_with_namespace")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    with Session(engine) as session:
        acc = session.scalars(select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == uid)).first()
        if not acc:
            raise HTTPException(404, "未绑定 Gitee 账号")
        token = acc.access_token
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    pulls = list_repo_open_pulls(token, pns, page=page, per_page=per_page)
    items = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        if (pr.get("state") or "").strip().lower() != "open":
            continue
        num = pr.get("number")
        try:
            num_int = int(num) if num is not None else 0
        except (TypeError, ValueError):
            num_int = 0
        html_url = pr.get("html_url") or ""
        if not html_url and num_int:
            html_url = f"https://gitee.com/{pns}/pulls/{num_int}"
        user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
        items.append(
            {
                "number": num_int,
                "title": (pr.get("title") or "")[:1024] if isinstance(pr.get("title"), str) else "",
                "html_url": str(html_url).strip(),
                "state": pr.get("state") or "open",
                "updated_at": pr.get("updated_at"),
                "author": (user.get("login") or "") if isinstance(user, dict) else "",
            }
        )
    return {
        "ok": True,
        "data": {
            "path_with_namespace": pns,
            "items": items,
            "page": page,
            "per_page": per_page,
            "has_more": len(pulls) >= per_page,
        },
    }


@router.post("/api/saas/gitee/onboarding/review")
def gitee_onboarding_review(
    request: Request,
    background_tasks: BackgroundTasks,
    body: GiteeOnboardingReviewBody,
):
    """
    用户主动对选中的 open 合并请求排队审查；使用用户默认 LLM 凭证或平台兜底。
    后台执行，与 WebHook 共用同一套审查与落库逻辑。
    """
    if not _saas_enable_gitee():
        raise HTTPException(403, "Gitee SaaS disabled on this server")
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    with Session(engine) as session:
        acc = session.scalars(select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == uid)).first()
        if not acc:
            raise HTTPException(404, "未绑定 Gitee 账号")
        token = acc.access_token
    seen: set[str] = set()
    for raw_u in body.pr_urls:
        u = (raw_u or "").strip()
        if not u or u in seen:
            continue
        if not u.startswith("https://gitee.com/"):
            raise HTTPException(400, "仅支持 https://gitee.com/ 下的合并请求链接")
        seen.add(u)
        background_tasks.add_task(run_saas_gitee_onboarding_review_for_url, uid, token, u)
    if not seen:
        raise HTTPException(400, "没有有效的链接")
    return {
        "ok": True,
        "data": {
            "queued": len(seen),
            "message": "已排队审查，请稍后到「报告」列表查看结果",
        },
    }
