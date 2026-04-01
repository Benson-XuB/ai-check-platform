"""ZIP 提交 job 的线程启动参数。"""

import tempfile
from pathlib import Path

import pytest

from app.services.prelaunch import pipeline


@pytest.fixture
def isolated_workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PRELAUNCH_WORKSPACE", td)
        yield Path(td)


def test_start_job_from_zip_passes_correct_thread_args(isolated_workspace, monkeypatch):
    import threading

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args=(), kwargs=None, daemon=None):
            captured["target"] = target
            captured["args"] = args
            captured["kwargs"] = kwargs or {}
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(threading, "Thread", FakeThread)

    jid = pipeline.start_job_from_zip(b"PK\x03\x04fake", "dashscope", "k")
    assert captured.get("started") is True
    assert captured["target"] == pipeline.run_prelaunch_pipeline
    assert captured["args"][0] == jid
    assert captured["kwargs"].get("zip_path")
