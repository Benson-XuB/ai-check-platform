from app.storage.models import AppUser, GitHubAppInstallation
from app.storage.db import create_db_engine


def test_github_installation_can_be_created_and_loaded(tmp_path, monkeypatch):
    # Use sqlite for unit tests
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")

    engine = create_db_engine()
    assert engine is not None

    from app.storage.models import Base
    from sqlalchemy.orm import Session

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        u = AppUser()
        session.add(u)
        session.flush()

        inst = GitHubAppInstallation(
            user_id=u.id,
            installation_id=123,
            account_login="acme",
            account_type="Organization",
        )
        session.add(inst)
        session.commit()

    with Session(engine) as session:
        got = session.query(GitHubAppInstallation).filter_by(installation_id=123).one()
        assert got.user_id is not None
        assert got.account_login == "acme"

