"""用户预设 LLM 凭证 CRUD、测试、设为默认（Gitee/GitHub 共用 session user_id）。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.llm_credential_crypto import decrypt_api_key, encrypt_api_key
from app.services.llm_ping import ping_preset
from app.services.llm_presets import get_preset, list_presets_public
from app.storage.db import create_db_engine
from app.storage.models import AppUser, UserLlmCredential

router = APIRouter(prefix="/api/saas/llm", tags=["saas-llm"])


def _session_user_id(request: Request) -> int:
    raw = request.session.get("user_id")
    if raw is None:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="请先登录")


def _require_db() -> None:
    if not create_db_engine():
        raise HTTPException(status_code=503, detail="无数据库")


class CredentialCreate(BaseModel):
    preset_id: str = Field(..., min_length=3, max_length=96)
    api_key: str = Field(..., min_length=1, max_length=2048)
    label: str = Field("", max_length=128)


class CredentialPatch(BaseModel):
    api_key: Optional[str] = Field(None, max_length=2048)
    label: Optional[str] = Field(None, max_length=128)


class TestBody(BaseModel):
    preset_id: Optional[str] = None
    api_key: Optional[str] = Field(None, max_length=2048)
    credential_id: Optional[int] = None


@router.get("/presets")
def llm_presets():
    return {"ok": True, "data": list_presets_public()}


@router.get("/credentials")
def list_credentials(request: Request):
    _require_db()
    uid = _session_user_id(request)
    engine = create_db_engine()
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        if not user:
            raise HTTPException(404, "用户不存在")
        rows = session.scalars(
            select(UserLlmCredential)
            .where(UserLlmCredential.user_id == uid)
            .order_by(UserLlmCredential.id.asc())
        ).all()
        active_id = user.active_llm_credential_id
        items = []
        for r in rows:
            preset = get_preset(r.preset_id)
            items.append(
                {
                    "id": r.id,
                    "preset_id": r.preset_id,
                    "label": r.label or "",
                    "display_label": preset.label_zh if preset else r.preset_id,
                    "provider": preset.provider if preset else "",
                    "api_model": preset.api_model if preset else "",
                    "is_active": r.id == active_id,
                    "has_key": bool(r.api_key_encrypted),
                }
            )
        return {"ok": True, "data": {"items": items, "active_credential_id": active_id}}


@router.post("/credentials")
def upsert_credential(request: Request, body: CredentialCreate):
    _require_db()
    uid = _session_user_id(request)
    preset = get_preset(body.preset_id.strip())
    if not preset:
        raise HTTPException(400, "未知的 preset_id")
    enc = encrypt_api_key(body.api_key.strip())
    engine = create_db_engine()
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        if not user:
            raise HTTPException(404, "用户不存在")
        row = session.scalars(
            select(UserLlmCredential).where(
                UserLlmCredential.user_id == uid,
                UserLlmCredential.preset_id == preset.id,
            )
        ).first()
        if row:
            row.api_key_encrypted = enc
            row.label = body.label.strip() or None
        else:
            row = UserLlmCredential(
                user_id=uid,
                preset_id=preset.id,
                api_key_encrypted=enc,
                label=body.label.strip() or None,
            )
            session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "data": {"id": row.id, "preset_id": row.preset_id}}


@router.patch("/credentials/{credential_id}")
def patch_credential(request: Request, credential_id: int, body: CredentialPatch):
    _require_db()
    uid = _session_user_id(request)
    engine = create_db_engine()
    with Session(engine) as session:
        row = session.get(UserLlmCredential, credential_id)
        if not row or row.user_id != uid:
            raise HTTPException(404, "凭证不存在")
        if body.api_key is not None and body.api_key.strip():
            row.api_key_encrypted = encrypt_api_key(body.api_key.strip())
        if body.label is not None:
            row.label = body.label.strip() or None
        session.commit()
        return {"ok": True}


@router.delete("/credentials/{credential_id}")
def delete_credential(request: Request, credential_id: int):
    _require_db()
    uid = _session_user_id(request)
    engine = create_db_engine()
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        row = session.get(UserLlmCredential, credential_id)
        if not row or row.user_id != uid:
            raise HTTPException(404, "凭证不存在")
        if user and user.active_llm_credential_id == credential_id:
            user.active_llm_credential_id = None
        session.delete(row)
        session.commit()
        return {"ok": True}


@router.post("/credentials/{credential_id}/activate")
def activate_credential(request: Request, credential_id: int):
    _require_db()
    uid = _session_user_id(request)
    engine = create_db_engine()
    with Session(engine) as session:
        user = session.get(AppUser, uid)
        row = session.get(UserLlmCredential, credential_id)
        if not user or not row or row.user_id != uid:
            raise HTTPException(404, "凭证不存在")
        user.active_llm_credential_id = row.id
        session.commit()
        return {"ok": True}


@router.post("/credentials/test")
def test_credential(request: Request, body: TestBody):
    _require_db()
    uid = _session_user_id(request)
    preset = None
    key = ""

    if body.credential_id is not None:
        engine = create_db_engine()
        with Session(engine) as session:
            row = session.get(UserLlmCredential, int(body.credential_id))
            if not row or row.user_id != uid:
                raise HTTPException(404, "凭证不存在")
            preset = get_preset(row.preset_id)
            if not preset:
                raise HTTPException(400, "凭证 preset 无效")
            try:
                key = decrypt_api_key(row.api_key_encrypted)
            except ValueError:
                raise HTTPException(400, "密钥解密失败")
    else:
        if not body.preset_id or not body.api_key or not body.api_key.strip():
            raise HTTPException(400, "需要 preset_id + api_key，或 credential_id")
        preset = get_preset(body.preset_id.strip())
        if not preset:
            raise HTTPException(400, "未知的 preset_id")
        key = body.api_key.strip()

    try:
        ping_preset(preset.provider, preset.api_model, key)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
