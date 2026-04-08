"""GitHub App 安装回调、SaaS 报告/回写（Agree 后发评论/Checks）。"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.db import create_db_engine
from app.storage.models import AppUser, GitHubAppInstallation, GitHubPrBinding, PrReviewReport

logger = logging.getLogger(__name__)

router = APIRouter(tags=["saas-github"])


def _require_db() -> None:
    if not create_db_engine():
        raise HTTPException(status_code=503, detail="SaaS 功能需要配置 DATABASE_URL（PostgreSQL 等）")


def _session_user_id(request: Request) -> Optional[int]:
    raw = request.session.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _github_app_install_url(state: str) -> str:
    """
    Prefer configured installation URL:
    - GITHUB_APP_INSTALL_URL: full URL to app installation page
    - or GITHUB_APP_SLUG: app slug (https://github.com/apps/<slug>/installations/new)
    """
    base = os.getenv("GITHUB_APP_INSTALL_URL", "").strip()
    slug = os.getenv("GITHUB_APP_SLUG", "").strip()
    if not base and slug:
        base = f"https://github.com/apps/{slug}/installations/new"
    if not base:
        raise HTTPException(503, "缺少 GITHUB_APP_INSTALL_URL 或 GITHUB_APP_SLUG")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}state={state}"


@router.get("/auth/github/install")
def github_install(request: Request):
    """
    Redirect user to GitHub App installation page.
    After installation, GitHub redirects to /auth/github/callback with installation_id + setup_action.
    """
    _require_db()
    state = secrets.token_urlsafe(32)
    request.session["gh_install_state"] = state
    return RedirectResponse(url=_github_app_install_url(state), status_code=302)


@router.get("/auth/github/callback")
def github_install_callback(
    request: Request,
    installation_id: Optional[int] = None,
    setup_action: Optional[str] = None,
    state: Optional[str] = None,
):
    _require_db()
    expected = request.session.get("gh_install_state")
    if not state or state != expected:
        raise HTTPException(400, "GitHub install state 无效")
    request.session.pop("gh_install_state", None)

    if installation_id is None:
        raise HTTPException(400, "缺少 installation_id")

    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")

    uid = _session_user_id(request)
    with Session(engine) as session:
        user: Optional[AppUser] = session.get(AppUser, uid) if uid else None
        if not user:
            user = AppUser()
            session.add(user)
            session.flush()
            request.session["user_id"] = user.id

        inst = session.scalars(
            select(GitHubAppInstallation).where(GitHubAppInstallation.installation_id == int(installation_id))
        ).first()
        if inst:
            inst.user_id = user.id
        else:
            inst = GitHubAppInstallation(user_id=user.id, installation_id=int(installation_id))
            session.add(inst)
        session.commit()

    logger.info("GitHub App installed setup_action=%s installation_id=%s", setup_action, installation_id)
    return RedirectResponse(url="/app", status_code=302)


@router.post("/auth/logout")
def logout(request: Request):
    """通用退出登录（清空 session）。"""
    request.session.clear()
    return {"ok": True}


@router.get("/api/saas/me")
def saas_me_github(request: Request):
    """SaaS 账号状态（GitHub App 模式）。"""
    uid = _session_user_id(request)
    if not uid:
        return {"ok": True, "authenticated": False}
    engine = create_db_engine()
    if not engine:
        return {"ok": False, "error": "no database"}
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        inst = session.scalars(select(GitHubAppInstallation).where(GitHubAppInstallation.user_id == uid)).first()
        if not user or not inst:
            request.session.clear()
            return {"ok": True, "authenticated": False}
        return {
            "ok": True,
            "authenticated": True,
            "user": {
                "id": user.id,
                "github_installation_id": inst.installation_id,
                "saas_webhook_token": user.saas_webhook_token,
            },
        }


@router.get("/api/saas/reports")
def saas_reports_github(request: Request, limit: int = 50, offset: int = 0):
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with Session(engine) as session:
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


@router.get("/api/saas/reports/{report_id}")
def saas_report_detail_github(request: Request, report_id: int):
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


@router.post("/api/saas/github/reports/{report_id}/agree")
def github_agree_postback(request: Request, report_id: int, idx: Optional[int] = None):
    """
    User clicks Agree in SaaS UI:
    - verify session user owns report
    - post stored comments back to GitHub PR
    """
    uid = _session_user_id(request)
    if not uid:
        raise HTTPException(401, "请先登录")
    _require_db()
    engine = create_db_engine()
    if not engine:
        raise HTTPException(503, "无数据库")

    from app.services.github_postback import post_report_comments_to_github

    with Session(engine) as session:
        r = session.get(PrReviewReport, int(report_id))
        if not r or r.user_id != uid:
            raise HTTPException(404, "报告不存在")
        b = session.scalars(select(GitHubPrBinding).where(GitHubPrBinding.report_id == r.id)).first()
        if not b:
            # self-heal: try to reconstruct binding for new report if missing
            inst = session.scalars(select(GitHubAppInstallation).where(GitHubAppInstallation.user_id == uid)).first()
            if not inst:
                raise HTTPException(404, "GitHub 绑定不存在")
            pns = (r.path_with_namespace or "").strip()
            if "/" not in pns:
                raise HTTPException(404, "GitHub 绑定不存在")
            owner, repo = pns.split("/", 1)
            b = GitHubPrBinding(
                report_id=r.id,
                user_id=uid,
                installation_id=int(inst.installation_id),
                owner=owner,
                repo=repo,
                pr_number=int(r.pr_number),
                head_sha=r.head_sha,
                check_run_id=None,
                posted_at=None,
            )
            session.add(b)
            session.commit()
            session.refresh(b)
        if b.user_id != uid:
            raise HTTPException(404, "GitHub 绑定不存在")
        out = post_report_comments_to_github(session, report=r, binding=b, only_idx=idx)
        if not out.get("ok"):
            raise HTTPException(502, str(out.get("error") or "postback failed"))
        return {"ok": True, "data": out}

