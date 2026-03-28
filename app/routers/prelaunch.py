"""上线前整仓扫描 API。"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.services.llm_defaults import get_public_default_llm_provider
from app.services.prelaunch import store
from app.services.prelaunch.config import get_max_repo_mb
from app.services.prelaunch.health import scanner_status
from app.services.prelaunch.pipeline import start_job, start_job_from_zip
from app.services.prelaunch.rate_limit import enforce_prelaunch_job_rate_limit
from app.services.prelaunch.schemas import JobStatus

router = APIRouter(prefix="/api/prelaunch", tags=["prelaunch"])


class CreatePrelaunchJobBody(BaseModel):
    repo_url: str = Field(..., min_length=8, description="HTTPS Git 地址")
    git_token: Optional[str] = None
    ref: Optional[str] = Field(None, description="分支或 tag，默认仓库默认分支")
    llm_provider: str = Field(default_factory=get_public_default_llm_provider)
    llm_api_key: str = Field(..., min_length=1, description="对应默认厂商的 API Key（用户自备）")


@router.get("/health")
def prelaunch_health():
    return scanner_status()


@router.post("/jobs")
def create_prelaunch_job(request: Request, body: CreatePrelaunchJobBody):
    enforce_prelaunch_job_rate_limit(request)
    job_id = start_job(
        body.repo_url,
        body.git_token,
        body.ref,
        body.llm_provider,
        body.llm_api_key,
    )
    return {"ok": True, "job_id": job_id}


@router.post("/jobs/zip")
async def create_prelaunch_job_zip(
    request: Request,
    file: UploadFile = File(...),
    llm_provider: Optional[str] = Form(None),
    llm_api_key: str = Form(..., min_length=1),
):
    enforce_prelaunch_job_rate_limit(request)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 文件")
    max_b = get_max_repo_mb() * 1024 * 1024
    content = await file.read()
    if len(content) > max_b:
        raise HTTPException(
            status_code=400,
            detail=f"ZIP 超过大小限制（当前上限 {get_max_repo_mb()} MB，可用 PRELAUNCH_MAX_REPO_MB 调整）",
        )
    prov = (llm_provider or "").strip() or get_public_default_llm_provider()
    job_id = start_job_from_zip(content, prov, llm_api_key)
    return {"ok": True, "job_id": job_id}


@router.get("/jobs/{job_id}")
def get_prelaunch_job(job_id: str):
    rec = store.load_record(job_id)
    if not rec:
        raise HTTPException(404, "job not found")
    d = rec.model_dump()
    for k in (
        "repo_path",
        "raw_paths",
        "normalized_path",
        "llm_report_path",
        "html_report_path",
        "pdf_report_path",
    ):
        d.pop(k, None)
    d["report_ready"] = rec.status == JobStatus.complete and bool(rec.html_report_path)
    d["pdf_ready"] = rec.status == JobStatus.complete and bool(rec.pdf_report_path)
    return {"ok": True, "data": d}


@router.get("/jobs/{job_id}/report", response_class=HTMLResponse)
def get_prelaunch_report_html(job_id: str):
    rec = store.load_record(job_id)
    if not rec or rec.status != JobStatus.complete or not rec.html_report_path:
        raise HTTPException(404, "report not ready")
    p = Path(rec.html_report_path)
    if not p.is_file():
        raise HTTPException(404, "report file missing")
    return HTMLResponse(p.read_text(encoding="utf-8"))


@router.get("/jobs/{job_id}/report.pdf")
def get_prelaunch_report_pdf(job_id: str):
    rec = store.load_record(job_id)
    if not rec or rec.status != JobStatus.complete or not rec.pdf_report_path:
        raise HTTPException(
            404,
            "PDF 不可用（可能未安装 WeasyPrint 或生成失败）。请使用 HTML 报告浏览器打印为 PDF。",
        )
    p = Path(rec.pdf_report_path)
    if not p.is_file():
        raise HTTPException(404, "pdf missing")
    return FileResponse(str(p), media_type="application/pdf", filename=f"prelaunch-{job_id}.pdf")
