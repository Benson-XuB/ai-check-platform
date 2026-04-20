"""custom_endpoint_completion、probe_custom_completion_backend（无网络：mock）。"""

from types import SimpleNamespace

import pytest

from app.services.llm_litellm import (
    _custom_auth_like_error,
    _exception_or_cause_auth_like,
    custom_endpoint_completion,
    probe_custom_completion_backend,
)


class _Exc401(Exception):
    status_code = 401


class _Exc403(Exception):
    status_code = 403


class _Exc500(Exception):
    status_code = 500


def test_custom_auth_like_error_status_codes():
    assert _custom_auth_like_error(_Exc401()) is True
    assert _custom_auth_like_error(_Exc403()) is True
    assert _custom_auth_like_error(_Exc500()) is False


def test_custom_auth_like_error_message_heuristic():
    e = RuntimeError("401 unauthorized: invalid key")
    assert _custom_auth_like_error(e) is True
    assert _custom_auth_like_error(RuntimeError("500 server error")) is False


def test_exception_or_cause_auth_like_chain():
    inner = _Exc401()
    outer = RuntimeError("wrap")
    outer.__cause__ = inner
    assert _exception_or_cause_auth_like(outer) is True


def _fake_moonshot_response(text: str):
    msg = SimpleNamespace(content=text, reasoning_content="")
    ch = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[ch])


def test_custom_endpoint_litellm_backend(monkeypatch):
    calls: list[tuple] = []

    def fake_litellm(model, api_key, prompt, **kw):
        calls.append((model, kw.get("base_url")))
        return "ok-litellm"

    monkeypatch.setattr("app.services.llm_litellm.litellm_completion", fake_litellm)

    out = custom_endpoint_completion(
        "sk-test",
        "https://api.example.com",
        "my-model",
        "hi",
        max_tokens=10,
        temperature=0.1,
        timeout=5.0,
        completion_backend="litellm",
    )
    assert out == "ok-litellm"
    assert len(calls) == 1
    assert calls[0][0] == "openai/my-model"
    assert calls[0][1] == "https://api.example.com/v1"


def test_custom_endpoint_anthropic_backend(monkeypatch):
    seen: list[str] = []

    def fake_anthropic(key, base, model, prompt, **kw):
        seen.append((base, model))
        return "from-anthropic"

    monkeypatch.setattr("app.services.llm_litellm._anthropic_messages_chat", fake_anthropic)

    def boom(*a, **k):
        raise AssertionError("should not call litellm when backend=anthropic")

    monkeypatch.setattr("app.services.llm_litellm.litellm_completion", boom)

    out = custom_endpoint_completion(
        "sk-test",
        "https://api.kimi.com/coding",
        "kimi-ai-coding",
        "ping",
        max_tokens=10,
        temperature=0.1,
        timeout=5.0,
        completion_backend="anthropic",
    )
    assert out == "from-anthropic"
    assert seen == [("https://api.kimi.com/coding", "kimi-ai-coding")]


def test_custom_endpoint_anthropic_backend_raises(monkeypatch):
    def fake_anthropic(*a, **k):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr("app.services.llm_litellm._anthropic_messages_chat", fake_anthropic)

    def boom(*a, **k):
        raise AssertionError("backend fixed anthropic should not call litellm")

    monkeypatch.setattr("app.services.llm_litellm.litellm_completion", boom)

    with pytest.raises(RuntimeError, match="自定义端点调用失败"):
        custom_endpoint_completion(
            "sk-test",
            "https://api.kimi.com/coding",
            "kimi-ai-coding",
            "ping",
            max_tokens=10,
            temperature=0.1,
            timeout=5.0,
            completion_backend="anthropic",
        )


def test_probe_prefers_anthropic(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_litellm._anthropic_messages_chat",
        lambda *a, **k: "ok",
    )

    def boom(*a, **k):
        raise AssertionError("probe should not call litellm when anthropic succeeds")

    monkeypatch.setattr("app.services.llm_litellm.litellm_completion", boom)

    b = probe_custom_completion_backend("sk", "https://api.kimi.com/coding", "m", timeout=5.0)
    assert b == "anthropic"


def test_probe_falls_back_litellm(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_litellm._anthropic_messages_chat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no anthropic")),
    )
    monkeypatch.setattr(
        "app.services.llm_litellm.litellm_completion",
        lambda *a, **k: "ok",
    )

    b = probe_custom_completion_backend("sk", "https://api.example.com", "m", timeout=5.0)
    assert b == "litellm"


def test_probe_both_fail(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_litellm._anthropic_messages_chat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a")),
    )
    monkeypatch.setattr(
        "app.services.llm_litellm.litellm_completion",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("b")),
    )

    with pytest.raises(RuntimeError, match="自定义端点不可用"):
        probe_custom_completion_backend("sk", "https://x.com", "m", timeout=5.0)


def test_custom_endpoint_empty_model():
    with pytest.raises(ValueError, match="empty api_model"):
        custom_endpoint_completion(
            "k",
            "https://api.openai.com/v1",
            "  ",
            "p",
            max_tokens=1,
            temperature=0.0,
            timeout=1.0,
            completion_backend="litellm",
        )
