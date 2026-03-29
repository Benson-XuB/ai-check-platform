"""Gitee WebHook：接收合并请求事件，后台触发拉取与 AI 审查。"""

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import gitee_webhook as wh_svc
from app.services.gitee_saas import process_saas_merge_request_webhook
from app.storage.db import create_db_engine
from app.storage.models import AppUser, GiteeOAuthAccount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gitee", tags=["gitee-webhook"])


@router.post("/webhook")
async def gitee_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Gitee 仓库 WebHook URL 指向此地址（POST）。
    环境变量：GITEE_WEBHOOK_SECRET（推荐）、GITEE_TOKEN、KIMI_API_KEY（默认厂商）或 DASHSCOPE_API_KEY（WEBHOOK_LLM_PROVIDER=dashscope 时）。
    可选：WEBHOOK_ENRICH_CONTEXT、WEBHOOK_AUTO_POST_COMMENTS、WEBHOOK_LLM_PROVIDER（默认 dashscope）等，见 app/services/gitee_webhook.py。
    """
    try:
        body = await request.body()
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JSONResponse({"ok": False, "error": f"invalid json: {e}"}, status_code=400)

    hdrs = {k.lower(): v for k, v in request.headers.items()}
    webhook_secret = os.getenv("GITEE_WEBHOOK_SECRET", "")
    if not wh_svc.verify_gitee_webhook(hdrs, webhook_secret):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    if not wh_svc.should_handle_merge_request_webhook(payload):
        return {"ok": True, "accepted": False, "ignored": True}

    background_tasks.add_task(wh_svc.process_merge_request_webhook, payload)
    return {"ok": True, "accepted": True}


@router.post("/webhook/saas/{route_token}")
async def gitee_webhook_saas(route_token: str, request: Request, background_tasks: BackgroundTasks):
    """
    按用户路由的 WebHook（URL 中含 saas_webhook_token）。
    使用用户 OAuth token 拉 PR，平台环境变量 LLM Key 审查，写入报告；不向 Gitee 发帖。
    """
    try:
        body = await request.body()
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JSONResponse({"ok": False, "error": f"invalid json: {e}"}, status_code=400)

    hdrs = {k.lower(): v for k, v in request.headers.items()}
    webhook_secret = os.getenv("GITEE_WEBHOOK_SECRET", "")
    if not wh_svc.verify_gitee_webhook(hdrs, webhook_secret):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    engine = create_db_engine()
    if not engine:
        return JSONResponse({"ok": False, "error": "database not configured"}, status_code=503)

    with Session(engine) as session:
        user = session.scalars(
            select(AppUser).where(AppUser.saas_webhook_token == route_token)
        ).first()
        if not user:
            return JSONResponse({"ok": False, "error": "unknown route token"}, status_code=404)
        acc = session.scalars(
            select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == user.id)
        ).first()
        if not acc:
            return JSONResponse({"ok": False, "error": "gitee account not linked"}, status_code=404)
        uid = user.id
        gitee_token = acc.access_token

    if not wh_svc.should_handle_merge_request_webhook(payload):
        return {"ok": True, "accepted": False, "ignored": True}

    background_tasks.add_task(
        process_saas_merge_request_webhook,
        payload,
        app_user_id=uid,
        gitee_access_token=gitee_token,
    )
    return {"ok": True, "accepted": True}
