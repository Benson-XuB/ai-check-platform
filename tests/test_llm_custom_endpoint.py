"""custom_endpoint_completion 与鉴权类错误判定（无网络：mock 下游调用）。"""

from types import SimpleNamespace

import pytest

from app.services.llm_litellm import (
    _custom_auth_like_error,
    _exception_or_cause_auth_like,
    custom_endpoint_completion,
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


def test_custom_endpoint_openai_only_non_kimi(monkeypatch):
    calls: list[tuple] = []

    def fake_moonshot(base, api_key, api_model, prompt, **kw):
        calls.append((base, api_model))
        return _fake_moonshot_response("ok-openai")

    monkeypatch.setattr(
        "app.services.llm_litellm._moonshot_chat_single",
        fake_moonshot,
    )
    out = custom_endpoint_completion(
        "sk-test",
        "https://api.openai.com",
        "gpt-4o-mini",
        "hi",
        max_tokens=10,
        temperature=0.1,
        timeout=5.0,
    )
    assert out == "ok-openai"
    assert len(calls) == 1
    assert calls[0][0] == "https://api.openai.com/v1"
    assert calls[0][1] == "gpt-4o-mini"


def test_custom_endpoint_kimi_coding_anthropic_success(monkeypatch):
    def fake_anthropic(key, model, prompt, **kw):
        return "from-anthropic"

    monkeypatch.setattr(
        "app.services.llm_litellm._kimi_code_anthropic_chat",
        fake_anthropic,
    )

    def boom(*a, **k):
        raise AssertionError("should not call OpenAI when Anthropic succeeds")

    monkeypatch.setattr("app.services.llm_litellm._moonshot_chat_single", boom)

    out = custom_endpoint_completion(
        "sk-test",
        "https://api.kimi.com/coding",
        "kimi-for-coding",
        "ping",
        max_tokens=10,
        temperature=0.1,
        timeout=5.0,
    )
    assert out == "from-anthropic"


def test_custom_endpoint_kimi_coding_falls_back_on_non_auth(monkeypatch):
    def fake_anthropic(*a, **k):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr(
        "app.services.llm_litellm._kimi_code_anthropic_chat",
        fake_anthropic,
    )

    def fake_moonshot(base, api_key, api_model, prompt, **kw):
        assert "/v1" in base
        return _fake_moonshot_response("from-openai")

    monkeypatch.setattr(
        "app.services.llm_litellm._moonshot_chat_single",
        fake_moonshot,
    )

    out = custom_endpoint_completion(
        "sk-test",
        "https://api.kimi.com/coding",
        "kimi-for-coding",
        "ping",
        max_tokens=10,
        temperature=0.1,
        timeout=5.0,
    )
    assert out == "from-openai"


def test_custom_endpoint_kimi_coding_no_fallback_on_401(monkeypatch):
    def fake_anthropic(*a, **k):
        raise _Exc401()

    monkeypatch.setattr(
        "app.services.llm_litellm._kimi_code_anthropic_chat",
        fake_anthropic,
    )

    def boom(*a, **k):
        raise AssertionError("OpenAI should not run after auth-like failure")

    monkeypatch.setattr("app.services.llm_litellm._moonshot_chat_single", boom)

    with pytest.raises(RuntimeError, match="自定义端点调用失败"):
        custom_endpoint_completion(
            "sk-bad",
            "https://api.kimi.com/coding",
            "kimi-for-coding",
            "ping",
            max_tokens=10,
            temperature=0.1,
            timeout=5.0,
        )


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
        )
