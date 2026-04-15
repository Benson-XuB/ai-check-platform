"""静态 HTML 响应：避免浏览器/反代长期缓存控制台页面导致看不到新版本入口。"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import FileResponse

_HTML_NO_CACHE = {
    "Cache-Control": "no-store, max-age=0, must-revalidate",
    "Pragma": "no-cache",
}


def html_file(path: Path) -> FileResponse:
    return FileResponse(path, headers=_HTML_NO_CACHE)
