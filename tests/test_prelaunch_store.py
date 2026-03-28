"""Prelaunch job 存储测试。"""

import json
import tempfile
import uuid
from pathlib import Path

import pytest

from app.services.prelaunch.schemas import JobStatus
from app.services.prelaunch import store


@pytest.fixture
def isolated_workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PRELAUNCH_WORKSPACE", td)
        yield Path(td)


def test_create_load_update(isolated_workspace):
    jid = uuid.uuid4().hex[:12]
    rec = store.create_job_record(jid, "https://gitee.com/a/b.git", "main")
    assert rec.job_id == jid
    assert rec.status == JobStatus.pending
    loaded = store.load_record(jid)
    assert loaded is not None
    assert loaded.repo_url_display == "https://gitee.com/a/b.git"

    store.update_job(jid, status=JobStatus.complete, normalized_count=3)
    again = store.load_record(jid)
    assert again.status == JobStatus.complete
    assert again.normalized_count == 3

    p = store.job_json_path(jid)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["job_id"] == jid
