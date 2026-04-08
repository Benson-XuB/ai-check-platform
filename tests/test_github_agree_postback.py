import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def test_agree_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/saas/github/reports/1/agree")
    assert r.status_code == 401


def test_agree_posts_and_marks_posted(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_APP_INSTALL_URL", "https://github.com/apps/x/installations/new")

    from app.main import app
    from app.storage.db import create_db_engine
    from app.storage.models import AppUser, Base, GitHubAppInstallation, GitHubPrBinding, GitHubPostedComment, PrReviewReport
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    assert engine is not None
    Base.metadata.create_all(
        engine,
        tables=[
            AppUser.__table__,
            GitHubAppInstallation.__table__,
            PrReviewReport.__table__,
            GitHubPrBinding.__table__,
            GitHubPostedComment.__table__,
        ],
    )

    client = TestClient(app)

    # Login/create session user via install callback flow
    r0 = client.get("/auth/github/install", follow_redirects=False)
    state = (r0.headers.get("location") or "").split("state=", 1)[1].split("&", 1)[0]
    client.get(f"/auth/github/callback?installation_id=77&state={state}", follow_redirects=False)

    with Session(engine) as session:
        uid = session.query(GitHubAppInstallation).filter_by(installation_id=77).one().user_id
        rep = PrReviewReport(
            user_id=uid,
            path_with_namespace="o/r",
            pr_number=1,
            head_sha="abc",
            pr_title="t",
            status="completed",
            result_json=json.dumps({"comments": [{"file": "a.py", "line": 1, "suggestion": "hi"}]}),
            error=None,
        )
        session.add(rep)
        session.flush()
        bind = GitHubPrBinding(
            report_id=rep.id,
            user_id=uid,
            installation_id=77,
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="abc",
            check_run_id=None,
            posted_at=None,
        )
        session.add(bind)
        session.commit()
        report_id = rep.id

    # patch token + post_comment
    import app.services.github_postback as postback

    monkeypatch.setattr(postback, "get_installation_token", lambda installation_id: "tok")

    import app.services.vcs_dispatch as vcs

    calls = {"n": 0}

    def _fake_post_comment(platform, owner, repo, number, comment, token, **kwargs):
        assert platform == "github"
        assert token == "tok"
        calls["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(vcs, "post_comment", _fake_post_comment)

    r = client.post(f"/api/saas/github/reports/{report_id}/agree")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert calls["n"] == 1

    with Session(engine) as session:
        b2 = session.query(GitHubPrBinding).filter_by(report_id=report_id).one()
        assert b2.posted_at is not None
        assert isinstance(b2.posted_at, datetime)


def test_agree_single_item_is_deduped(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("GITHUB_APP_INSTALL_URL", "https://github.com/apps/x/installations/new")

    from app.main import app
    from app.storage.db import create_db_engine
    from app.storage.models import AppUser, Base, GitHubAppInstallation, GitHubPrBinding, GitHubPostedComment, PrReviewReport
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    assert engine is not None
    Base.metadata.create_all(
        engine,
        tables=[
            AppUser.__table__,
            GitHubAppInstallation.__table__,
            PrReviewReport.__table__,
            GitHubPrBinding.__table__,
            GitHubPostedComment.__table__,
        ],
    )

    client = TestClient(app)
    r0 = client.get("/auth/github/install", follow_redirects=False)
    state = (r0.headers.get("location") or "").split("state=", 1)[1].split("&", 1)[0]
    client.get(f"/auth/github/callback?installation_id=77&state={state}", follow_redirects=False)

    with Session(engine) as session:
        uid = session.query(GitHubAppInstallation).filter_by(installation_id=77).one().user_id
        rep = PrReviewReport(
            user_id=uid,
            path_with_namespace="o/r",
            pr_number=1,
            head_sha="abc",
            pr_title="t",
            status="completed",
            result_json=json.dumps({"comments": [{"file": "a.py", "line": 1, "suggestion": "hi"}]}),
            error=None,
        )
        session.add(rep)
        session.flush()
        bind = GitHubPrBinding(
            report_id=rep.id,
            user_id=uid,
            installation_id=77,
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="abc",
            check_run_id=None,
            posted_at=None,
        )
        session.add(bind)
        session.commit()
        report_id = rep.id

    import app.services.github_postback as postback
    monkeypatch.setattr(postback, "get_installation_token", lambda installation_id: "tok")

    import app.services.vcs_dispatch as vcs
    calls = {"n": 0}

    def _fake_post_comment(platform, owner, repo, number, comment, token, **kwargs):
        calls["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(vcs, "post_comment", _fake_post_comment)

    r1 = client.post(f"/api/saas/github/reports/{report_id}/agree?idx=0")
    assert r1.status_code == 200
    r2 = client.post(f"/api/saas/github/reports/{report_id}/agree?idx=0")
    assert r2.status_code == 200
    assert calls["n"] == 1

