"""Kimi / Moonshot base URL 顺序（无网络）。"""

from types import SimpleNamespace

from app.services.llm_litellm import (
    _anthropic_message_blocks_to_text,
    _is_kimi_coding_base,
    _kimi_coding_user_agent,
    _kimi_moonshot_endpoints_to_try,
    _moonshot_assistant_text,
    _use_kimi_code_anthropic,
)


def test_sk_kimi_prefix_prefers_coding_first(monkeypatch):
    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    bases = _kimi_moonshot_endpoints_to_try("sk-kimi-test")
    assert bases[0] == "https://api.kimi.com/coding/v1"
    assert "https://api.moonshot.cn/v1" in bases
    assert "https://api.moonshot.ai/v1" in bases


def test_other_key_prefers_official_first(monkeypatch):
    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    bases = _kimi_moonshot_endpoints_to_try("sk-not-kimi")
    assert bases[0] == "https://api.moonshot.cn/v1"
    assert bases[-1] == "https://api.kimi.com/coding/v1"


def test_explicit_moonshot_ai_first_when_non_kimi_key(monkeypatch):
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    bases = _kimi_moonshot_endpoints_to_try("sk-abc")
    assert bases[0] == "https://api.moonshot.ai/v1"
    assert bases[1] == "https://api.moonshot.cn/v1"
    assert bases[-1] == "https://api.kimi.com/coding/v1"


def test_is_kimi_coding_base():
    assert _is_kimi_coding_base("https://api.kimi.com/coding/v1")
    assert not _is_kimi_coding_base("https://api.moonshot.cn/v1")


def test_kimi_coding_user_agent_default_and_empty_opt_out(monkeypatch):
    monkeypatch.delenv("KIMI_CODING_USER_AGENT", raising=False)
    assert _kimi_coding_user_agent() == "claude-code/0.1.0"
    monkeypatch.setenv("KIMI_CODING_USER_AGENT", "")
    assert _kimi_coding_user_agent() is None


def test_moonshot_assistant_text_reasoning_fallback():
    msg = SimpleNamespace(content="", reasoning_content="hello")
    assert _moonshot_assistant_text(msg) == "hello"
    msg2 = SimpleNamespace(content="x", reasoning_content="y")
    assert _moonshot_assistant_text(msg2) == "x"


def test_use_kimi_code_anthropic_only_kimi_for_coding():
    assert _use_kimi_code_anthropic("kimi-for-coding") is True
    assert _use_kimi_code_anthropic("moonshot-v1-32k") is False


def test_anthropic_message_blocks_to_text():
    class TB:
        type = "text"
        text = "hi"

    class Msg:
        content = [TB()]

    assert _anthropic_message_blocks_to_text(Msg()) == "hi"
