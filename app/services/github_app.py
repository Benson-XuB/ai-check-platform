"""GitHub App 工具：webhook 验签、App JWT、installation token。"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Optional

import httpx

# PyJWT is only required for GitHub App JWT/token exchange.
# Keep import optional so pure webhook signature tests can run
# in minimal environments.
try:
    import jwt  # type: ignore
except Exception:  # noqa: BLE001
    jwt = None  # type: ignore


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def normalize_pem(raw: str) -> str:
    """
    Normalize PEM private key from env:
    - Supports literal newlines
    - Supports '\\n' escaped newlines (common in Railway/CI)
    """
    s = (raw or "").strip()
    if "\\n" in s and "\n" not in s:
        s = s.replace("\\n", "\n")
    return s


def verify_github_webhook(headers: dict[str, str], body: bytes, secret: str) -> bool:
    """
    Verify GitHub webhook signature.
    GitHub sends: X-Hub-Signature-256: sha256=<hex>
    """
    sec = (secret or "").encode("utf-8")
    if not sec:
        return False
    sig = ""
    for k, v in headers.items():
        if k.lower() == "x-hub-signature-256":
            sig = (v or "").strip()
            break
    if not sig.startswith("sha256="):
        return False
    want = sig.split("=", 1)[1].strip()
    mac = hmac.new(sec, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, mac)


def github_api_base() -> str:
    return _env("GITHUB_API_BASE") or "https://api.github.com"


def app_jwt(*, now: Optional[int] = None) -> str:
    """
    Create GitHub App JWT.
    Env:
    - GITHUB_APP_ID
    - GITHUB_APP_PRIVATE_KEY (PEM)
    """
    if jwt is None:
        raise RuntimeError("PyJWT not installed (missing dependency)")

    app_id = _env("GITHUB_APP_ID")
    if not app_id:
        raise RuntimeError("missing GITHUB_APP_ID")
    pem = normalize_pem(_env("GITHUB_APP_PRIVATE_KEY"))
    if not pem:
        raise RuntimeError("missing GITHUB_APP_PRIVATE_KEY")
    t = int(now if now is not None else time.time())
    payload = {
        "iat": t - 5,
        "exp": t + 9 * 60,  # <= 10 minutes
        "iss": app_id,
    }
    return jwt.encode(payload, pem, algorithm="RS256")  # type: ignore[no-any-return]


def get_installation_token(installation_id: int) -> str:
    """
    Exchange App JWT for installation access token.
    """
    jid = int(installation_id)
    tok = app_jwt()
    base = github_api_base().rstrip("/")
    url = f"{base}/app/installations/{jid}/access_tokens"
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.post(url, headers=headers, json={})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"failed to create installation token: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("token") if isinstance(data, dict) else None
    if not token or not isinstance(token, str):
        raise RuntimeError("invalid installation token response")
    return token


def installation_auth_headers(installation_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {installation_token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

