import os

import httpx


def test_exchange_code_for_token_retries_on_timeout(monkeypatch):
    from app.services import gitee_saas

    monkeypatch.setenv("GITEE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GITEE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GITEE_OAUTH_REDIRECT_URI", "https://example.com/cb")
    monkeypatch.setenv("GITEE_HTTP_RETRIES", "2")

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectTimeout("handshake timeout")

            class R:
                status_code = 200

                def json(self):
                    return {"access_token": "t"}

            return R()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = gitee_saas.exchange_code_for_token("code")
    assert out["access_token"] == "t"
    assert calls["n"] == 2

