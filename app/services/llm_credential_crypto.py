"""用户 LLM API Key 加密存储（Fernet）。"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    raw = (os.getenv("LLM_CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    if raw:
        try:
            return Fernet(raw.encode() if isinstance(raw, str) else raw)
        except Exception as e:
            logger.warning("LLM_CREDENTIAL_ENCRYPTION_KEY invalid: %s", e)
    #开发回退：由 SESSION_SECRET 派生（生产务必设置独立密钥）
    sec = (os.getenv("SESSION_SECRET") or "dev-session-secret-change-in-production").encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(sec + b"|llm-cred-v1").digest())
    return Fernet(key)


def encrypt_api_key(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_api_key(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ValueError("credential decrypt failed") from None
