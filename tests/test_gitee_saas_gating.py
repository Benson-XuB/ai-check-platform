from fastapi.testclient import TestClient


def test_gitee_saas_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from app.main import app

    client = TestClient(app)
    r = client.get("/auth/gitee/login")
    assert r.status_code == 404


def test_gitee_saas_can_be_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("SAAS_ENABLE_GITEE", "1")
    from app.main import app

    client = TestClient(app)
    r = client.get("/api/saas/gitee/me")
    # no session -> unauthenticated but endpoint exists
    assert r.status_code == 200
    assert r.json()["authenticated"] is False

