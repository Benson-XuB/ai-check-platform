"""端到端流水线（同步，建议在后台线程运行）。"""

import json
import shutil
import traceback
import uuid
from pathlib import Path
from typing import Optional

from app.services.prelaunch import store
from app.services.prelaunch.detect import detect_project, profile_hints_for_report
from app.services.prelaunch.git_clone import clone_repo, inject_git_token, redact_repo_url
from app.services.prelaunch.heuristics import run_repo_heuristics
from app.services.prelaunch.llm_enrich import enrich_llm_from_findings
from app.services.prelaunch.llm_report import generate_llm_report_with_stages
from app.services.prelaunch.mvp_buckets import apply_mvp_buckets
from app.services.prelaunch.normalize import dedupe_findings
from app.services.prelaunch.parsers import parse_all
from app.services.prelaunch.pdf_export import html_to_pdf
from app.services.prelaunch.render import render_html_report
from app.services.prelaunch.runners import run_all_scanners
from app.services.prelaunch.schemas import JobStatus, PrelaunchJobRecord
from app.services.prelaunch.zip_extract import extract_uploaded_zip


def run_prelaunch_pipeline(
    job_id: str,
    repo_url: str,
    git_token: Optional[str],
    ref: Optional[str],
    llm_provider: str,
    llm_api_key: str,
    *,
    zip_path: Optional[str] = None,
) -> None:
    jdir = store.job_dir(job_id)
    repo_path = jdir / "repo"
    try:
        store.update_job(job_id, status=JobStatus.cloning, error=None)
        if repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        if zip_path:
            zp = Path(zip_path)
            if not zp.is_file():
                raise RuntimeError("ZIP 文件不存在或已删除")
            extract_uploaded_zip(zp, repo_path)
        else:
            if not (repo_url or "").strip():
                raise RuntimeError("请提供 Git 仓库 URL 或上传 ZIP")
            clone_url = inject_git_token(repo_url, git_token)
            clone_repo(clone_url, repo_path, ref)

        store.update_job(job_id, status=JobStatus.scanning, repo_path=str(repo_path))
        profile = detect_project(repo_path)
        run_all_scanners(repo_path, jdir, profile)

        store.update_job(job_id, status=JobStatus.normalizing)
        raw = parse_all(jdir)
        raw.extend(run_repo_heuristics(repo_path, profile))
        findings = dedupe_findings(raw)
        findings = apply_mvp_buckets(findings)
        norm_path = jdir / "normalized.json"
        norm_path.write_text(
            json.dumps([f.model_dump() for f in findings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.update_job(job_id, normalized_path=str(norm_path), normalized_count=len(findings))

        store.update_job(job_id, status=JobStatus.llm)
        llm, gonogo_blob = generate_llm_report_with_stages(
            findings, profile, repo_path, llm_provider, llm_api_key
        )
        enrich_llm_from_findings(llm, findings)
        llm_path = jdir / "llm_report.json"
        llm_path.write_text(llm.model_dump_json(indent=2), encoding="utf-8")
        store.update_job(job_id, llm_report_path=str(llm_path))
        if gonogo_blob is not None:
            gpath = jdir / "gonogo_stages.json"
            gpath.write_text(json.dumps(gonogo_blob, ensure_ascii=False, indent=2), encoding="utf-8")

        store.update_job(job_id, status=JobStatus.rendering)
        rec = store.load_record(job_id)
        if not rec:
            raise RuntimeError("job record missing")
        hints = profile_hints_for_report(profile)
        html = render_html_report(rec, findings, llm, hints)
        html_path = jdir / "report.html"
        html_path.write_text(html, encoding="utf-8")
        store.update_job(job_id, html_report_path=str(html_path))

        pdf_path = jdir / "report.pdf"
        if html_to_pdf(html, pdf_path):
            store.update_job(job_id, pdf_report_path=str(pdf_path))

        store.update_job(job_id, status=JobStatus.complete)
    except Exception as e:
        store.update_job(job_id, status=JobStatus.failed, error=f"{e}\n{traceback.format_exc()[-4000:]}")


def start_job(
    repo_url: str,
    git_token: Optional[str],
    ref: Optional[str],
    llm_provider: str,
    llm_api_key: str,
) -> str:
    job_id = uuid.uuid4().hex[:16]
    display = redact_repo_url(repo_url)
    store.create_job_record(job_id, display, ref)
    # 延迟导入避免循环
    import threading

    t = threading.Thread(
        target=run_prelaunch_pipeline,
        args=(job_id, repo_url, git_token, ref, llm_provider, llm_api_key),
        daemon=True,
    )
    t.start()
    return job_id


def start_job_from_zip(
    zip_bytes: bytes,
    llm_provider: str,
    llm_api_key: str,
) -> str:
    job_id = uuid.uuid4().hex[:16]
    store.create_job_record(job_id, "本地上传 ZIP", None)
    jdir = store.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "upload.zip").write_bytes(zip_bytes)
    import threading

    zp = str((jdir / "upload.zip").resolve())
    t = threading.Thread(
        target=run_prelaunch_pipeline,
        args=(job_id, "", None, None, llm_provider, llm_api_key),
        kwargs={"zip_path": zp},
        daemon=True,
    )
    t.start()
    return job_id
