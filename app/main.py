"""FastAPI 主应用：AI PR Review SaaS。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.middleware.max_body import MaxRequestBodySizeMiddleware
from app.routers import gitee, gitee_webhook, prelaunch, rag, review, vcs
from app.storage.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from app.services.prelaunch.cleanup import cleanup_expired_jobs

        cleanup_expired_jobs()
    except Exception:
        pass
    yield


app = FastAPI(
    title="AI PR Review",
    description="多平台 PR 审查与 Prelaunch 上线前扫描；默认 LLM 为通义（可环境变量覆盖）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 最后注册、最外层：先校验 Content-Length，避免超大 JSON/表单占满内存
app.add_middleware(MaxRequestBodySizeMiddleware)

app.include_router(gitee.router)
app.include_router(vcs.router)
app.include_router(gitee_webhook.router)
app.include_router(rag.router)
app.include_router(review.router)
app.include_router(prelaunch.router)

STATIC_DIR = Path(__file__).parent.parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/prelaunch")
def prelaunch_page():
    return FileResponse(STATIC_DIR / "prelaunch.html")
