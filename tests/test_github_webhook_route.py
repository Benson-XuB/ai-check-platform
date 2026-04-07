import hashlib
import hmac
import json

from fastapi.testclient import TestClient


def _sig(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def test_github_webhook_rejects_bad_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "sec")

    from app.main import app

    client = TestClient(app)
    body = json.dumps({"action": "opened"}).encode("utf-8")
    r = client.post(
        "/api/github/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert r.status_code == 401


def test_github_webhook_accepts_and_routes_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "sec")

    from app.main import app
    from app.storage.db import create_db_engine
    from app.storage.models import AppUser, Base, GitHubAppInstallation
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    assert engine is not None
    Base.metadata.create_all(engine, tables=[AppUser.__table__, GitHubAppInstallation.__table__])

    with Session(engine) as session:
        u = AppUser()
        session.add(u)
        session.flush()
        session.add(GitHubAppInstallation(user_id=u.id, installation_id=99))
        session.commit()

    # monkeypatch background processor to avoid network
    called = {"ok": False}

    def _fake(payload, *, app_user_id: int, installation_id: int):
        called["ok"] = True
        assert app_user_id == u.id
        assert installation_id == 99

    import app.routers.github_webhook as gh_router

    monkeypatch.setattr(gh_router, "process_saas_github_pull_request_webhook", _fake)

    client = TestClient(app)
    payload = {
        "action": "opened",
        "installation": {"id": 99},
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "head": {"sha": "abc"}, "html_url": "https://github.com/o/r/pull/1"},
    }
    body = json.dumps(payload).encode("utf-8")
    r = client.post(
        "/api/github/webhook",
        data=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sig("sec", body),
        },
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert called["ok"] is True

