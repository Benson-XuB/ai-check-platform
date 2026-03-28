"""Prelaunch 建任务频控。"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app.services.prelaunch.rate_limit import (
    enforce_prelaunch_job_rate_limit,
    reset_prelaunch_rate_limit_state,
)


def _fake_request(client_host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/prelaunch/jobs",
        "headers": [],
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_rate_limit_allows_then_blocks(monkeypatch):
    reset_prelaunch_rate_limit_state()
    monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_WINDOW_SEC", "3600")
    req = _fake_request("192.168.1.100")
    enforce_prelaunch_job_rate_limit(req)
    enforce_prelaunch_job_rate_limit(req)
    with pytest.raises(HTTPException) as ei:
        enforce_prelaunch_job_rate_limit(req)
    assert ei.value.status_code == 429
    reset_prelaunch_rate_limit_state()


def test_rate_limit_disabled(monkeypatch):
    reset_prelaunch_rate_limit_state()
    monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_MAX", "0")
    req = _fake_request("192.168.1.101")
    for _ in range(20):
        enforce_prelaunch_job_rate_limit(req)
    reset_prelaunch_rate_limit_state()


def test_xff_first_hop(monkeypatch):
    reset_prelaunch_rate_limit_state()
    monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_MAX", "1")
    monkeypatch.setenv("PRELAUNCH_RATE_LIMIT_WINDOW_SEC", "3600")
    monkeypatch.setenv("PRELAUNCH_TRUST_X_FORWARDED_FOR", "1")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        "client": ("127.0.0.1", 80),
    }
    req = Request(scope)
    enforce_prelaunch_job_rate_limit(req)
    with pytest.raises(HTTPException) as ei:
        enforce_prelaunch_job_rate_limit(req)
    assert ei.value.status_code == 429
    monkeypatch.delenv("PRELAUNCH_TRUST_X_FORWARDED_FOR", raising=False)
    reset_prelaunch_rate_limit_state()
