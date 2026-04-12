import pytest


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "unit-test-session-secret")


def test_signed_oauth_state_round_trip():
    from app.services.oauth_state import make_signed_oauth_state, verify_signed_oauth_state

    s = make_signed_oauth_state()
    assert verify_signed_oauth_state(s) is True


def test_signed_oauth_state_rejects_tamper():
    from app.services.oauth_state import make_signed_oauth_state, verify_signed_oauth_state

    s = make_signed_oauth_state()
    raw, sig = s.rsplit(".", 1)
    bad = raw + ".0" * len(sig)
    assert verify_signed_oauth_state(bad) is False


def test_signed_oauth_state_rejects_none_and_empty():
    from app.services.oauth_state import verify_signed_oauth_state

    assert verify_signed_oauth_state(None) is False
    assert verify_signed_oauth_state("") is False


def test_signed_oauth_state_secret_rotation_invalidates_old_token(monkeypatch):
    from app.services.oauth_state import make_signed_oauth_state, verify_signed_oauth_state

    s = make_signed_oauth_state()
    monkeypatch.setenv("SESSION_SECRET", "other-secret")
    assert verify_signed_oauth_state(s) is False
