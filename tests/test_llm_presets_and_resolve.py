"""用户 LLM 预设（无网络）。"""

from app.services.llm_presets import get_preset, list_presets_public
from app.services.platform_llm import platform_llm_key


def test_preset_catalog_has_domestic_and_intl():
    ids = {p["id"] for p in list_presets_public()}
    assert "dashscope:qwen-plus" in ids
    assert "kimi:moonshot-v1-32k" in ids
    assert "openai:gpt-4o-mini" in ids
    assert "anthropic:claude-3-5-sonnet-20241022" in ids
    assert "deepseek-official:deepseek-chat" in ids
    row = next(x for x in list_presets_public() if x["id"] == "openai:gpt-4o-mini")
    assert row["group"]
    assert row["provider"] == "litellm"


def test_get_preset_litellm_provider():
    p = get_preset("openai:gpt-4o")
    assert p is not None
    assert p.provider == "litellm"
    assert p.api_model == "openai/gpt-4o"


def test_platform_llm_key_reads_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEFAULT_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    prov, key = platform_llm_key()
    assert prov == "dashscope"
    assert key == "sk-test"
