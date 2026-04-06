"""运维诊断接口：仅用于排查出站网络问题（默认关闭）。"""

from __future__ import annotations

import os
import time
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/diag", tags=["diag"])


def _diag_enabled() -> bool:
    return os.getenv("DIAG_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


AllowedTarget = Literal["home", "api_user"]


def _target_url(target: AllowedTarget) -> str:
    # 白名单，避免任何形式的 SSRF
    if target == "home":
        return "https://gitee.com/"
    if target == "api_user":
        return "https://gitee.com/api/v5/user"
    raise ValueError("unsupported target")


@router.get("/egress/gitee")
def diag_egress_gitee(target: AllowedTarget = "home") -> dict[str, Any]:
    """
    探测容器出站到 gitee.com 的 TLS 握手/网络超时问题。

    - 仅当 DIAG_ENABLED=1 时可用
    - 仅允许访问固定白名单 URL（避免 SSRF）
    """
    if not _diag_enabled():
        raise HTTPException(status_code=404, detail="not found")

    url = _target_url(target)
    timeout_sec = 12.0
    started = time.monotonic()
    err: Optional[BaseException] = None
    status_code: Optional[int] = None
    response_headers: dict[str, str] = {}

    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_sec),
            follow_redirects=True,
            headers={"User-Agent": "ai-check-platform/diag-egress"},
        ) as client:
            r = client.get(url)
            status_code = r.status_code
            # 只回少量头，避免噪声/敏感信息
            for k in ("server", "date", "content-type", "cf-ray", "x-request-id"):
                if k in r.headers:
                    response_headers[k] = r.headers.get(k, "")
    except Exception as e:  # noqa: BLE001 - 诊断接口需要回传异常信息
        err = e

    elapsed_ms = int((time.monotonic() - started) * 1000)

    payload: dict[str, Any] = {
        "ok": err is None and status_code is not None,
        "target": target,
        "url": url,
        "timeout_sec": timeout_sec,
        "elapsed_ms": elapsed_ms,
        "status_code": status_code,
        "headers": response_headers,
    }
    if err is not None:
        payload["error_type"] = type(err).__name__
        payload["error"] = str(err)
    return payload

