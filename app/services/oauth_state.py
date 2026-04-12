"""Signed OAuth ``state`` (CSRF) without relying on session cookies.

Session-backed state breaks when the browser does not send the session cookie on the
callback (www vs apex domain, copied callback URL, some mobile/in-app browsers).
Uses the same secret as SessionMiddleware (SESSION_SECRET).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


def _signing_key() -> bytes:
    return (os.getenv("SESSION_SECRET") or "dev-session-secret-change-in-production").encode("utf-8")


def make_signed_oauth_state() -> str:
    raw = secrets.token_urlsafe(32)
    sig = hmac.new(_signing_key(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_signed_oauth_state(state: str | None) -> bool:
    if not state or "." not in state:
        return False
    raw, sig = state.rsplit(".", 1)
    if not raw or not sig:
        return False
    expected = hmac.new(_signing_key(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
