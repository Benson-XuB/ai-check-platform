"""清理超时的 Prelaunch 任务目录（仅已完成/失败，按 created_at）。"""

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.services.prelaunch.config import get_job_ttl_hours, get_workspace_root
from app.services.prelaunch.schemas import JobStatus

logger = logging.getLogger(__name__)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def cleanup_expired_jobs() -> int:
    """
    删除 created_at 早于 TTL 的 complete/failed 任务目录。
    返回删除的目录数。
    """
    root = get_workspace_root()
    if not root.is_dir():
        return 0
    ttl_h = get_job_ttl_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_h)
    removed = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        jp = d / "job.json"
        if not jp.is_file():
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = data.get("status")
        if status not in (JobStatus.complete.value, JobStatus.failed.value):
            continue
        created = _parse_iso(data.get("created_at") or "")
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            try:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
            except Exception as e:
                logger.warning("prelaunch cleanup failed for %s: %s", d, e)
    if removed:
        logger.info("prelaunch cleanup removed %s job dir(s) older than %sh", removed, ttl_h)
    return removed
