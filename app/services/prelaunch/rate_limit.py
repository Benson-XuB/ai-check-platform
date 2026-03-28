"""Prelaunch 建任务频控（namespace 独立，不与 PR 审查等共用配额）。"""

import os

from fastapi import Request

from app.services.sliding_rate_limit import clear_namespace, enforce


def _max_hits() -> int:
    try:
        return int(os.getenv("PRELAUNCH_RATE_LIMIT_MAX", "8"))
    except ValueError:
        return 8


def _window_sec() -> int:
    try:
        return max(60, int(os.getenv("PRELAUNCH_RATE_LIMIT_WINDOW_SEC", "3600")))
    except ValueError:
        return 3600


def reset_prelaunch_rate_limit_state() -> None:
    clear_namespace("prelaunch:job")


def enforce_prelaunch_job_rate_limit(request: Request) -> None:
    cap = _max_hits()
    if cap <= 0:
        return
    window = _window_sec()
    enforce(
        request,
        namespace="prelaunch:job",
        max_hits=cap,
        window_sec=window,
        detail=(
            f"本服务为上线前体检，已限制频率：每 {window // 60} 分钟最多 {cap} 次检查（每 IP）。"
            "请稍后再试，或联系管理员调整 PRELAUNCH_RATE_LIMIT_MAX / WINDOW。"
        ),
    )
