from fastapi.testclient import TestClient


def test_gitee_saas_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("SAAS_DISABLE_GITEE", "1")
    from app.main import app

    client = TestClient(app)
    r = client.get("/auth/gitee/login")
    assert r.status_code == 403
    assert "Gitee" in r.text


def test_gitee_saas_enabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.delenv("SAAS_DISABLE_GITEE", raising=False)
    monkeypatch.delenv("SAAS_ENABLE_GITEE", raising=False)
    from app.main import app

    client = TestClient(app)
    r = client.get("/api/saas/gitee/me")
    # no session -> unauthenticated but endpoint exists; not disabled
    assert r.status_code == 200
    assert r.json()["authenticated"] is False
    assert r.json().get("disabled") is not True

