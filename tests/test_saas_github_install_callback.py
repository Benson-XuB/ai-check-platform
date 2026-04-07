from fastapi.testclient import TestClient


def test_github_install_callback_requires_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_APP_INSTALL_URL", "https://github.com/apps/x/installations/new")

    from app.main import app

    client = TestClient(app)
    r = client.get("/auth/github/callback?installation_id=1&state=bad")
    assert r.status_code == 400


def test_github_install_callback_creates_user_and_binds_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_APP_INSTALL_URL", "https://github.com/apps/x/installations/new")

    from app.main import app
    from app.storage.db import create_db_engine
    from app.storage.models import AppUser, Base, GitHubAppInstallation
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    assert engine is not None
    # Only create the few tables we need; some optional features use Postgres-only types (e.g. JSONB).
    Base.metadata.create_all(engine, tables=[AppUser.__table__, GitHubAppInstallation.__table__])

    client = TestClient(app)

    # Call install endpoint to set session + capture state from redirect URL
    r0 = client.get("/auth/github/install", follow_redirects=False)
    assert r0.status_code == 302
    loc = r0.headers.get("location") or ""
    assert "state=" in loc
    state = loc.split("state=", 1)[1].split("&", 1)[0]

    r = client.get(f"/auth/github/callback?installation_id=42&state={state}", follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        inst = session.query(GitHubAppInstallation).filter_by(installation_id=42).one()
        assert inst.user_id is not None

