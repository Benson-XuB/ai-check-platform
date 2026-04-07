import json

from fastapi.testclient import TestClient


def test_saas_reports_are_available_when_gitee_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_APP_INSTALL_URL", "https://github.com/apps/x/installations/new")

    from app.main import app
    from app.storage.db import create_db_engine
    from app.storage.models import AppUser, Base, GitHubAppInstallation, PrReviewReport
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    assert engine is not None
    Base.metadata.create_all(engine, tables=[AppUser.__table__, GitHubAppInstallation.__table__, PrReviewReport.__table__])

    client = TestClient(app)
    r0 = client.get("/auth/github/install", follow_redirects=False)
    state = (r0.headers.get("location") or "").split("state=", 1)[1].split("&", 1)[0]
    client.get(f"/auth/github/callback?installation_id=123&state={state}", follow_redirects=False)

    with Session(engine) as session:
        uid = session.query(GitHubAppInstallation).filter_by(installation_id=123).one().user_id
        session.add(
            PrReviewReport(
                user_id=uid,
                path_with_namespace="o/r",
                pr_number=1,
                head_sha="abc",
                pr_title="t",
                status="completed",
                result_json=json.dumps({"comments": []}),
                error=None,
            )
        )
        session.commit()

    r_list = client.get("/api/saas/reports?limit=50")
    assert r_list.status_code == 200
    items = r_list.json()["data"]["items"]
    assert len(items) == 1

