"""SaaS 审查：解析用户默认 LLM 凭证，失败则回退平台环境变量。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.platform_llm import platform_llm_key
from app.services.llm_credential_crypto import decrypt_api_key
from app.services.llm_presets import get_preset
from app.storage.db import create_db_engine
from app.storage.models import AppUser, UserLlmCredential

logger = logging.getLogger(__name__)

Source = Literal["user", "platform"]


def _normalize_custom_completion_backend(raw: Optional[str]) -> Optional[str]:
    s = (raw or "").strip().lower()
    if s in ("anthropic", "litellm"):
        return s
    return None


@dataclass
class ResolvedLlm:
    provider: str
    api_key: str
    api_model: Optional[str]
    source: Source
    # provider=custom 时已校验的 Base URL；预设凭证时为 None
    custom_base_url: Optional[str] = None
    # provider=custom：anthropic | litellm（保存凭证时探测写入）；未迁移的旧数据可能为 None
    custom_completion_backend: Optional[str] = None


def resolve_llm_for_review(app_user_id: int) -> ResolvedLlm:
    engine = create_db_engine()
    if engine:
        try:
            with Session(engine) as session:
                user = session.get(AppUser, app_user_id)
                if user and user.active_llm_credential_id:
                    cred = session.get(UserLlmCredential, user.active_llm_credential_id)
                    if cred and cred.user_id == user.id:
                        if getattr(cred, "is_custom", False) and cred.custom_base_url and cred.custom_model:
                            try:
                                key = decrypt_api_key(cred.api_key_encrypted)
                            except ValueError:
                                logger.warning(
                                    "user llm credential decrypt failed user=%s cred=%s; fallback platform",
                                    app_user_id,
                                    cred.id,
                                )
                            else:
                                if key.strip():
                                    logger.info(
                                        "SaaS review llm source=user custom user=%s cred=%s",
                                        app_user_id,
                                        cred.id,
                                    )
                                    return ResolvedLlm(
                                        "custom",
                                        key.strip(),
                                        cred.custom_model.strip(),
                                        "user",
                                        custom_base_url=cred.custom_base_url.strip(),
                                        custom_completion_backend=_normalize_custom_completion_backend(
                                            getattr(cred, "custom_completion_backend", None)
                                        ),
                                    )
                                logger.warning("empty user llm key user=%s cred=%s", app_user_id, cred.id)
                        else:
                            preset = get_preset(cred.preset_id)
                            if preset:
                                try:
                                    key = decrypt_api_key(cred.api_key_encrypted)
                                except ValueError:
                                    logger.warning(
                                        "user llm credential decrypt failed user=%s cred=%s; fallback platform",
                                        app_user_id,
                                        cred.id,
                                    )
                                else:
                                    if key.strip():
                                        logger.info(
                                            "SaaS review llm source=user user=%s preset=%s provider=%s",
                                            app_user_id,
                                            cred.preset_id,
                                            preset.provider,
                                        )
                                        return ResolvedLlm(
                                            preset.provider,
                                            key.strip(),
                                            preset.api_model,
                                            "user",
                                        )
                                    logger.warning("empty user llm key user=%s cred=%s", app_user_id, cred.id)
                            else:
                                logger.warning("unknown preset_id=%s user=%s", cred.preset_id, app_user_id)
        except Exception:
            logger.exception("resolve_llm_for_review db error user=%s", app_user_id)

    provider, key = platform_llm_key()
    key = (key or "").strip()
    logger.info("SaaS review llm source=platform user=%s provider=%s has_key=%s", app_user_id, provider, bool(key))
    return ResolvedLlm(provider, key, None, "platform")
