"""GitHub App WebHook：接收 pull_request 事件，触发 SaaS 审查并创建 Check Run。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.github_app import verify_github_webhook
from app.services.github_saas import process_saas_github_pull_request_webhook, should_handle_github_pull_request_event
from app.storage.db import create_db_engine
from app.storage.models import GitHubAppInstallation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["github-webhook"])


def _secret() -> str:
    return (os.getenv("GITHUB_WEBHOOK_SECRET") or "").strip()


def _event_name(request: Request) -> str:
    return (request.headers.get("X-GitHub-Event") or "").strip()


@router.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JSONResponse({"ok": False, "error": f"invalid json: {e}"}, status_code=400)

    sec = _secret()
    if not sec:
        # Safer default: require secret for GitHub webhook
        return JSONResponse({"ok": False, "error": "webhook secret not configured"}, status_code=503)

    hdrs = {k: v for k, v in request.headers.items()}
    if not verify_github_webhook(hdrs, body, sec):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    event = _event_name(request)
    if not should_handle_github_pull_request_event(event, payload):
        return {"ok": True, "accepted": False, "ignored": True}

    installation = payload.get("installation") or {}
    inst_id = installation.get("id")
    try:
        installation_id = int(inst_id) if inst_id is not None else 0
    except (TypeError, ValueError):
        installation_id = 0
    if not installation_id:
        raise HTTPException(400, "missing installation.id")

    engine = create_db_engine()
    if not engine:
        return JSONResponse({"ok": False, "error": "database not configured"}, status_code=503)

    app_user_id: Optional[int] = None
    with Session(engine) as session:
        row = session.scalars(
            select(GitHubAppInstallation).where(GitHubAppInstallation.installation_id == installation_id)
        ).first()
        if row:
            app_user_id = row.user_id
    if not app_user_id:
        return JSONResponse({"ok": False, "error": "unknown installation_id"}, status_code=404)

    background_tasks.add_task(
        process_saas_github_pull_request_webhook,
        payload,
        app_user_id=app_user_id,
        installation_id=installation_id,
    )
    return {"ok": True, "accepted": True}

