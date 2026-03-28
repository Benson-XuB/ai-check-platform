"""Job 状态持久化：每任务目录下 job.json。"""

import json
from pathlib import Path
from typing import Optional

from app.services.prelaunch.config import get_workspace_root
from app.services.prelaunch.schemas import JobStatus, PrelaunchJobRecord, utc_now_iso


def job_dir(job_id: str) -> Path:
    return get_workspace_root() / job_id


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def save_record(record: PrelaunchJobRecord) -> None:
    record.touch()
    p = job_json_path(record.job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(record.model_dump_json(indent=2), encoding="utf-8")


def load_record(job_id: str) -> Optional[PrelaunchJobRecord]:
    p = job_json_path(job_id)
    if not p.is_file():
        return None
    try:
        return PrelaunchJobRecord.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def create_job_record(job_id: str, repo_url_display: str, ref: Optional[str]) -> PrelaunchJobRecord:
    now = utc_now_iso()
    rec = PrelaunchJobRecord(
        job_id=job_id,
        status=JobStatus.pending,
        repo_url_display=repo_url_display,
        ref=ref,
        created_at=now,
        updated_at=now,
    )
    save_record(rec)
    return rec


def update_job(job_id: str, **kwargs) -> Optional[PrelaunchJobRecord]:
    rec = load_record(job_id)
    if not rec:
        return None
    for k, v in kwargs.items():
        if hasattr(rec, k):
            setattr(rec, k, v)
    save_record(rec)
    return rec
