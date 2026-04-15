"""FastAPI 主应用：AI PR Review SaaS。"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from dotenv import load_dotenv

    # 包目录（ai-check-platform/）；多数人在仓库根 prreview/ 放 .env，也需能读到
    _PKG_ROOT = Path(__file__).resolve().parent.parent
    _REPO_ROOT = _PKG_ROOT.parent
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(_PKG_ROOT / ".env")
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from app.middleware.max_body import MaxRequestBodySizeMiddleware
from app.routers import diag, gitee, gitee_webhook, github_webhook, prelaunch, rag, review, saas_gitee, saas_github, saas_llm_credentials, vcs
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
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-session-secret-change-in-production"),
    same_site="lax",
    https_only=os.getenv("SESSION_HTTPS_ONLY", "").strip().lower() in ("1", "true", "yes"),
)

app.include_router(gitee.router)
app.include_router(vcs.router)
app.include_router(gitee_webhook.router)
app.include_router(github_webhook.router)
app.include_router(rag.router)
app.include_router(review.router)
app.include_router(prelaunch.router)
app.include_router(saas_gitee.router)
app.include_router(saas_github.router)
app.include_router(saas_llm_credentials.router)
app.include_router(diag.router)

STATIC_DIR = Path(__file__).parent.parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manual")
def manual_review_page():
    """自备 Token / LLM Key / PR 链接的手动审查表单。"""
    return FileResponse(STATIC_DIR / "manual-review.html")


@app.get("/prelaunch")
def prelaunch_page():
    return FileResponse(STATIC_DIR / "prelaunch.html")


@app.get("/app")
def saas_app_page():
    """GitHub App：报告列表与 PR 回写。"""
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/app-gitee")
def saas_gitee_app_page():
    """Gitee 专用控制台（不影响 GitHub SaaS）。"""
    return FileResponse(STATIC_DIR / "app-gitee.html")


@app.get("/app-gitee/llm")
def saas_gitee_llm_page():
    """Gitee：模型预设与 API Key（独立页）。"""
    return FileResponse(STATIC_DIR / "app-gitee-llm.html")


@app.get("/app/llm")
def saas_github_llm_page():
    """GitHub：模型预设与 API Key（独立页）。"""
    return FileResponse(STATIC_DIR / "app-github-llm.html")


@app.get("/app-gitee/reports")
def saas_gitee_reports_page():
    """Gitee：审查报告分页列表。"""
    return FileResponse(STATIC_DIR / "app-gitee-reports.html")


@app.get("/app-gitee/onboarding")
def saas_gitee_onboarding_page():
    """Gitee：首次接入，手动选择已有 open 合并请求进行审查。"""
    return FileResponse(STATIC_DIR / "app-gitee-onboarding.html")


@app.get("/app/reports")
def saas_github_reports_page():
    """GitHub：审查报告分页列表。"""
    return FileResponse(STATIC_DIR / "app-github-reports.html")
