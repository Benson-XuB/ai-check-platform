"""SaaS API entrypoint (GitHub/Gitee PR review). Kept separate from Prelaunch."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from dotenv import load_dotenv

    _PKG_ROOT = Path(__file__).resolve().parents[2]
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
from app.routers import github_webhook, rag, review, saas_gitee, saas_github, vcs
from app.storage.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AI PR Review SaaS",
    description="GitHub/Gitee PR review SaaS API (separate service).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MaxRequestBodySizeMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-session-secret-change-in-production"),
    same_site="lax",
    https_only=os.getenv("SESSION_HTTPS_ONLY", "").strip().lower() in ("1", "true", "yes"),
)

app.include_router(vcs.router)
app.include_router(github_webhook.router)
app.include_router(rag.router)
app.include_router(review.router)
app.include_router(saas_gitee.router)
app.include_router(saas_github.router)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manual")
def manual_review_page():
    return FileResponse(STATIC_DIR / "manual-review.html")


@app.get("/app")
def saas_app_page():
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/app-gitee")
def saas_gitee_app_page():
    return FileResponse(STATIC_DIR / "app-gitee.html")

