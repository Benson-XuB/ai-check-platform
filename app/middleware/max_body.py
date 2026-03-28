"""按路径限制请求体大小（依赖 Content-Length；无则无法预先拒绝）。"""

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _mb(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _max_bytes_for_path(path: str) -> int:
    # ZIP 与 Prelaunch 配置对齐（multipart 略超纯文件，给一点余量）
    if path.rstrip("/") == "/api/prelaunch/jobs/zip" or path.endswith("/api/prelaunch/jobs/zip"):
        try:
            from app.services.prelaunch.config import get_max_repo_mb

            mb = get_max_repo_mb()
        except Exception:
            mb = 500
        return mb * 1024 * 1024 + 256 * 1024
    return _mb("HTTP_MAX_BODY_MB", 48) * 1024 * 1024


class MaxRequestBodySizeMiddleware(BaseHTTPMiddleware):
    """仅检查 Content-Length；chunked 大请求需反向代理层限制。"""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("content-length")
            if cl:
                try:
                    n = int(cl)
                except ValueError:
                    return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
                limit = _max_bytes_for_path(request.url.path)
                if n > limit:
                    return JSONResponse(
                        {
                            "detail": (
                                f"请求体超过上限（本路径约 {limit // (1024 * 1024)} MB）。"
                                "大 diff 可调 HTTP_MAX_BODY_MB；ZIP 调 PRELAUNCH_MAX_REPO_MB。"
                            )
                        },
                        status_code=413,
                    )
        return await call_next(request)
