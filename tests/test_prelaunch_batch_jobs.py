"""Prelaunch 批量创建 jobs。"""

import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.prelaunch import store


@pytest.fixture
def isolated_workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PRELAUNCH_WORKSPACE", td)
        monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_MAX", "0")
        yield Path(td)


def test_create_batch_jobs_returns_ids_and_creates_records(isolated_workspace, monkeypatch):
    import app.routers.prelaunch as prelaunch_router

    def fake_start_job(repo_url, git_token, ref, llm_provider, llm_api_key):
        jid = uuid.uuid4().hex[:12]
        store.create_job_record(jid, repo_url, ref)
        return jid

    monkeypatch.setattr(prelaunch_router, "start_job", fake_start_job)

    client = TestClient(app)
    r = client.post(
        "/api/prelaunch/jobs/batch",
        json={
            "repo_urls": ["https://github.com/acme/a", "https://github.com/acme/b"],
            "llm_provider": "dashscope",
            "llm_api_key": "x",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert len(data["job_ids"]) == 2
    for jid in data["job_ids"]:
        assert store.load_record(jid) is not None


def test_batch_rejects_all_empty_urls(isolated_workspace):
    client = TestClient(app)
    r = client.post(
        "/api/prelaunch/jobs/batch",
        json={
            "repo_urls": ["", "   "],
            "llm_provider": "dashscope",
            "llm_api_key": "x",
        },
    )
    assert r.status_code == 400
