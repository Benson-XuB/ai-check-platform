"""进程内滑动窗口限流：按 (namespace, client_ip)，多类接口互不抢配额。"""

import os
import threading
import time
from typing import Dict, List, Tuple

from fastapi import HTTPException, Request

_store: Dict[Tuple[str, str], List[float]] = {}
_lock = threading.Lock()


def reset_all_rate_limit_state() -> None:
    """测试或排障：清空全部计数。"""
    with _lock:
        _store.clear()


def clear_namespace(namespace: str) -> None:
    """只清空某一类限流（如单测）。"""
    with _lock:
        for k in list(_store.keys()):
            if k[0] == namespace:
                del _store[k]


def trust_x_forwarded_for() -> bool:
    return os.getenv("PRELAUNCH_TRUST_X_FORWARDED_FOR", "").lower() in ("1", "true", "yes") or os.getenv(
        "PUBLIC_TRUST_X_FORWARDED_FOR", ""
    ).lower() in ("1", "true", "yes")


def client_ip(request: Request) -> str:
    if trust_x_forwarded_for():
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce(
    request: Request,
    *,
    namespace: str,
    max_hits: int,
    window_sec: int,
    detail: str,
) -> None:
    """
    max_hits <= 0 表示关闭该 namespace 限流。
    window_sec 至少 60，避免误配成秒级风暴。
    """
    if max_hits <= 0:
        return
    window_sec = max(60, int(window_sec))
    ip = client_ip(request)
    key = (namespace, ip)
    now = time.time()
    with _lock:
        hits = _store.setdefault(key, [])
        hits[:] = [t for t in hits if now - t < window_sec]
        if len(hits) >= max_hits:
            raise HTTPException(status_code=429, detail=detail)
        hits.append(now)
