"""用户预设 LLM 凭证 CRUD、测试、设为默认（Gitee/GitHub 共用 session user_id）。"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.llm_credential_crypto import decrypt_api_key, encrypt_api_key
from app.services.llm_custom_url import validate_custom_base_url
from app.services.llm_ping import ping_custom_endpoint, ping_preset
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
    """预设模式：preset_id + api_key；自定义模式：custom_base_url + custom_model + api_key。"""

    preset_id: Optional[str] = Field(None, max_length=96)
    custom_base_url: Optional[str] = Field(None, max_length=2048)
    custom_model: Optional[str] = Field(None, max_length=256)
    api_key: str = Field(..., min_length=1, max_length=2048)
    label: str = Field("", max_length=128)

    @model_validator(mode="after")
    def preset_or_custom(self) -> "CredentialCreate":
        p = (self.preset_id or "").strip()
        cu = (self.custom_base_url or "").strip()
        cm = (self.custom_model or "").strip()
        if p and cu:
            raise ValueError("请只使用预设或只使用自定义端点，不要混填")
        if not p and (not cu or not cm):
            raise ValueError("请选择预设模型，或填写自定义 Base URL 与模型名称")
        return self


class CredentialPatch(BaseModel):
    api_key: Optional[str] = Field(None, max_length=2048)
    label: Optional[str] = Field(None, max_length=128)
    custom_base_url: Optional[str] = Field(None, max_length=2048)
    custom_model: Optional[str] = Field(None, max_length=256)


class TestBody(BaseModel):
    """与前端 JSON 字段一致；亦接受 camelCase（customBaseUrl / customModel）。"""

    model_config = ConfigDict(populate_by_name=True)

    preset_id: Optional[str] = None
    api_key: Optional[str] = Field(None, max_length=2048)
    credential_id: Optional[int] = None
    custom_base_url: Optional[str] = Field(
        None,
        max_length=2048,
        validation_alias=AliasChoices("custom_base_url", "customBaseUrl"),
    )
    custom_model: Optional[str] = Field(
        None,
        max_length=256,
        validation_alias=AliasChoices("custom_model", "customModel"),
    )


@router.get("/presets")
def llm_presets():
    return {"ok": True, "data": list_presets_public()}


def _credential_display(row: UserLlmCredential) -> tuple[str, str, str]:
    """display_label, provider, api_model（展示用）"""
    if getattr(row, "is_custom", False) and row.custom_base_url and row.custom_model:
        host = row.custom_base_url.strip()
        if len(host) > 48:
            host = host[:45] + "…"
        return (f"自定义 · {host} · {row.custom_model.strip()}", "custom", row.custom_model.strip())
    preset = get_preset(row.preset_id)
    if preset:
        return (preset.label_zh, preset.provider, preset.api_model)
    return (row.preset_id, "", "")


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
            disp, prov, am = _credential_display(r)
            items.append(
                {
                    "id": r.id,
                    "preset_id": r.preset_id,
                    "is_custom": bool(getattr(r, "is_custom", False)),
                    "custom_base_url": (r.custom_base_url or "") if getattr(r, "custom_base_url", None) else "",
                    "custom_model": (r.custom_model or "") if getattr(r, "custom_model", None) else "",
                    "custom_completion_backend": (getattr(r, "custom_completion_backend", None) or ""),
                    "label": r.label or "",
                    "display_label": disp,
                    "provider": prov,
                    "api_model": am,
                    "is_active": r.id == active_id,
                    "has_key": bool(r.api_key_encrypted),
                }
            )
        return {"ok": True, "data": {"items": items, "active_credential_id": active_id}}


@router.post("/credentials")
def upsert_credential(request: Request, body: CredentialCreate):
    _require_db()
    uid = _session_user_id(request)
    enc = encrypt_api_key(body.api_key.strip())
    engine = create_db_engine()

    p = (body.preset_id or "").strip()
    cu = (body.custom_base_url or "").strip()
    cm = (body.custom_model or "").strip()

    if cu and cm:
        try:
            validated = validate_custom_base_url(cu)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            backend = ping_custom_endpoint(validated, cm, body.api_key.strip())
        except Exception as e:
            raise HTTPException(400, f"测试未通过: {e}") from e
        preset_row_id = f"custom-{secrets.token_hex(16)}"
        with Session(engine) as session:
            user = session.get(AppUser, uid)
            if not user:
                raise HTTPException(404, "用户不存在")
            row = UserLlmCredential(
                user_id=uid,
                preset_id=preset_row_id,
                is_custom=True,
                custom_base_url=validated,
                custom_model=cm.strip(),
                custom_completion_backend=backend,
                api_key_encrypted=enc,
                label=body.label.strip() or None,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {
                "ok": True,
                "data": {
                    "id": row.id,
                    "preset_id": row.preset_id,
                    "is_custom": True,
                    "custom_completion_backend": backend,
                },
            }

    preset = get_preset(p)
    if not preset:
        raise HTTPException(400, "未知的 preset_id")
    try:
        ping_preset(preset.provider, preset.api_model, body.api_key.strip())
    except Exception as e:
        raise HTTPException(400, f"测试未通过: {e}") from e

    with Session(engine) as session:
        user = session.get(AppUser, uid)
        if not user:
            raise HTTPException(404, "用户不存在")
        row = session.scalars(
            select(UserLlmCredential).where(
                UserLlmCredential.user_id == uid,
                UserLlmCredential.preset_id == preset.id,
                UserLlmCredential.is_custom.is_(False),
            )
        ).first()
        if row:
            row.api_key_encrypted = enc
            row.label = body.label.strip() or None
        else:
            row = UserLlmCredential(
                user_id=uid,
                preset_id=preset.id,
                is_custom=False,
                custom_base_url=None,
                custom_model=None,
                api_key_encrypted=enc,
                label=body.label.strip() or None,
            )
            session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "data": {"id": row.id, "preset_id": row.preset_id, "is_custom": False}}


@router.patch("/credentials/{credential_id}")
def patch_credential(request: Request, credential_id: int, body: CredentialPatch):
    _require_db()
    uid = _session_user_id(request)
    engine = create_db_engine()
    with Session(engine) as session:
        row = session.get(UserLlmCredential, credential_id)
        if not row or row.user_id != uid:
            raise HTTPException(404, "凭证不存在")
        if getattr(row, "is_custom", False):
            new_url = body.custom_base_url if body.custom_base_url is not None else row.custom_base_url
            new_model = body.custom_model if body.custom_model is not None else row.custom_model
            if body.api_key is not None and body.api_key.strip():
                row.api_key_encrypted = encrypt_api_key(body.api_key.strip())
            need_ping = body.custom_base_url is not None or body.custom_model is not None or (
                body.api_key is not None and body.api_key.strip()
            )
            if need_ping:
                try:
                    validated = validate_custom_base_url((new_url or "").strip())
                except ValueError as e:
                    raise HTTPException(400, str(e)) from e
                cm = (new_model or "").strip()
                if not cm:
                    raise HTTPException(400, "custom_model 不能为空")
                try:
                    key_try = decrypt_api_key(row.api_key_encrypted)
                except ValueError:
                    raise HTTPException(400, "密钥解密失败") from None
                try:
                    backend = ping_custom_endpoint(validated, cm, key_try)
                except Exception as e:
                    raise HTTPException(400, f"测试未通过: {e}") from e
                row.custom_base_url = validated
                row.custom_model = cm
                row.custom_completion_backend = backend
        else:
            if body.api_key is not None and body.api_key.strip():
                row.api_key_encrypted = encrypt_api_key(body.api_key.strip())
            if body.custom_base_url is not None or body.custom_model is not None:
                raise HTTPException(400, "预设凭证不能改 custom_base_url/custom_model")
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
    cu = (body.custom_base_url or "").strip()
    cm = (body.custom_model or "").strip()

    if body.credential_id is not None:
        engine = create_db_engine()
        with Session(engine) as session:
            row = session.get(UserLlmCredential, int(body.credential_id))
            if not row or row.user_id != uid:
                raise HTTPException(404, "凭证不存在")
            try:
                key = decrypt_api_key(row.api_key_encrypted)
            except ValueError:
                raise HTTPException(400, "密钥解密失败")
            if getattr(row, "is_custom", False) and row.custom_base_url and row.custom_model:
                try:
                    bk = (getattr(row, "custom_completion_backend", None) or "").strip() or None
                    ping_custom_endpoint(
                        row.custom_base_url, row.custom_model, key, completion_backend=bk
                    )
                except Exception as e:
                    return {"ok": False, "error": str(e)}
                return {"ok": True}
            preset = get_preset(row.preset_id)
            if not preset:
                raise HTTPException(400, "凭证 preset 无效")
    elif cu or cm:
        if not cu or not cm:
            raise HTTPException(400, "自定义测试需同时提供 custom_base_url 与 custom_model")
        if not body.api_key or not body.api_key.strip():
            raise HTTPException(400, "自定义测试需提供 api_key")
        key = body.api_key.strip()
        try:
            validated = validate_custom_base_url(cu)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            ping_custom_endpoint(validated, cm, key)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}
    else:
        if not body.preset_id or not body.api_key or not body.api_key.strip():
            raise HTTPException(
                400,
                "需要 preset_id + api_key，或 custom_base_url + custom_model + api_key，或 credential_id",
            )
        preset = get_preset(body.preset_id.strip())
        if not preset:
            raise HTTPException(400, "未知的 preset_id")
        key = body.api_key.strip()

    try:
        ping_preset(preset.provider, preset.api_model, key)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
