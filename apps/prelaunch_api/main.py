"""Prelaunch API entrypoint (separate service)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import prelaunch


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prelaunch is file-workspace based; no DB init required.
    yield


app = FastAPI(
    title="Prelaunch",
    description="Prelaunch scan API (separate service).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prelaunch.router)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "prelaunch.html")

