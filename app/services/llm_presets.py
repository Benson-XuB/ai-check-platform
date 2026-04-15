"""用户可选 LLM 预设（服务端固定列表）。Kimi 走 Moonshot 官方兼容接口；其余国际/国内模型经 LiteLLM（model 写法见官方文档）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LlmPreset:
    id: str
    provider: str  # dashscope | kimi | litellm
    api_model: str
    label_zh: str
    group: str


# provider=litellm 时 api_model 为 LiteLLM 完整 model 字符串（含前缀）
_LL_PRESETS: tuple[LlmPreset, ...] = (
    # —— 国内 · 阿里云通义（DashScope / LiteLLM dashscope/）——
    LlmPreset("dashscope:qwen-turbo", "dashscope", "qwen-turbo", "通义 · qwen-turbo", "国内 · 通义千问"),
    LlmPreset("dashscope:qwen-plus", "dashscope", "qwen-plus", "通义 · qwen-plus", "国内 · 通义千问"),
    LlmPreset("dashscope:qwen-max", "dashscope", "qwen-max", "通义 · qwen-max", "国内 · 通义千问"),
    LlmPreset("dashscope:qwen-long", "dashscope", "qwen-long", "通义 · qwen-long", "国内 · 通义千问"),
    LlmPreset("dashscope:qwen-flash", "dashscope", "qwen-flash", "通义 · qwen-flash", "国内 · 通义千问"),
    LlmPreset(
        "dashscope:qwen2.5-7b-instruct",
        "dashscope",
        "qwen2.5-7b-instruct",
        "通义 · qwen2.5-7b-instruct",
        "国内 · 通义千问",
    ),
    LlmPreset(
        "dashscope:qwen2.5-14b-instruct",
        "dashscope",
        "qwen2.5-14b-instruct",
        "通义 · qwen2.5-14b-instruct",
        "国内 · 通义千问",
    ),
    LlmPreset(
        "dashscope:qwen2.5-32b-instruct",
        "dashscope",
        "qwen2.5-32b-instruct",
        "通义 · qwen2.5-32b-instruct",
        "国内 · 通义千问",
    ),
    LlmPreset(
        "dashscope:qwen2.5-72b-instruct",
        "dashscope",
        "qwen2.5-72b-instruct",
        "通义 · qwen2.5-72b-instruct",
        "国内 · 通义千问",
    ),
    LlmPreset("dashscope:qwen2.5-turbo", "dashscope", "qwen2.5-turbo", "通义 · qwen2.5-turbo", "国内 · 通义千问"),
    LlmPreset("dashscope:qwen3-32b", "dashscope", "qwen3-32b", "通义 · qwen3-32b", "国内 · 通义千问"),
    LlmPreset("dashscope:deepseek-v3", "dashscope", "deepseek-v3", "通义 · deepseek-v3", "国内 · 通义千问"),
    LlmPreset("dashscope:deepseek-r1", "dashscope", "deepseek-r1", "通义 · deepseek-r1", "国内 · 通义千问"),
    # —— 国内 · Kimi（Moonshot OpenAI 兼容，不经 LiteLLM）——
    LlmPreset("kimi:moonshot-v1-8k", "kimi", "moonshot-v1-8k", "Kimi · moonshot-v1-8k", "国内 · Kimi"),
    LlmPreset("kimi:moonshot-v1-32k", "kimi", "moonshot-v1-32k", "Kimi · moonshot-v1-32k", "国内 · Kimi"),
    LlmPreset("kimi:moonshot-v1-128k", "kimi", "moonshot-v1-128k", "Kimi · moonshot-v1-128k", "国内 · Kimi"),
    LlmPreset("kimi:moonshot-v1-auto", "kimi", "moonshot-v1-auto", "Kimi · moonshot-v1-auto", "国内 · Kimi"),
    LlmPreset(
        "kimi:kimi-k2-0711-preview",
        "kimi",
        "kimi-k2-0711-preview",
        "Kimi · kimi-k2-0711-preview",
        "国内 · Kimi",
    ),
    # —— 国内 · DeepSeek 官方 API（LiteLLM）——
    LlmPreset(
        "deepseek-official:deepseek-chat",
        "litellm",
        "deepseek/deepseek-chat",
        "DeepSeek 官方 · deepseek-chat",
        "国内 · DeepSeek",
    ),
    LlmPreset(
        "deepseek-official:deepseek-reasoner",
        "litellm",
        "deepseek/deepseek-reasoner",
        "DeepSeek 官方 · deepseek-reasoner",
        "国内 · DeepSeek",
    ),
    # —— 国内 · 智谱 Z.AI（LiteLLM 前缀 zai/，密钥见文档）——
    LlmPreset("zhipu:glm-4.7", "litellm", "zai/glm-4.7", "智谱 · GLM-4.7", "国内 · 智谱"),
    LlmPreset("zhipu:glm-4.5-air", "litellm", "zai/glm-4.5-air", "智谱 · GLM-4.5-air", "国内 · 智谱"),
    LlmPreset("zhipu:glm-4.5-flash", "litellm", "zai/glm-4.5-flash", "智谱 · GLM-4.5-flash", "国内 · 智谱"),
    # —— 国际 · OpenAI（LiteLLM）——
    LlmPreset("openai:gpt-4o", "litellm", "openai/gpt-4o", "OpenAI · gpt-4o", "国际 · OpenAI"),
    LlmPreset("openai:gpt-4o-mini", "litellm", "openai/gpt-4o-mini", "OpenAI · gpt-4o-mini", "国际 · OpenAI"),
    LlmPreset("openai:gpt-4-turbo", "litellm", "openai/gpt-4-turbo", "OpenAI · gpt-4-turbo", "国际 · OpenAI"),
    LlmPreset("openai:o1-mini", "litellm", "openai/o1-mini", "OpenAI · o1-mini", "国际 · OpenAI"),
    LlmPreset("openai:o3-mini", "litellm", "openai/o3-mini", "OpenAI · o3-mini", "国际 · OpenAI"),
    # —— 国际 · Anthropic Claude（LiteLLM）——
    LlmPreset(
        "anthropic:claude-sonnet-4-20250514",
        "litellm",
        "anthropic/claude-sonnet-4-20250514",
        "Anthropic · Claude Sonnet 4",
        "国际 · Anthropic",
    ),
    LlmPreset(
        "anthropic:claude-3-5-sonnet-20241022",
        "litellm",
        "anthropic/claude-3-5-sonnet-20241022",
        "Anthropic · Claude 3.5 Sonnet",
        "国际 · Anthropic",
    ),
    LlmPreset(
        "anthropic:claude-3-5-haiku-20241022",
        "litellm",
        "anthropic/claude-3-5-haiku-20241022",
        "Anthropic · Claude 3.5 Haiku",
        "国际 · Anthropic",
    ),
    LlmPreset(
        "anthropic:claude-3-opus-20240229",
        "litellm",
        "anthropic/claude-3-opus-20240229",
        "Anthropic · Claude 3 Opus",
        "国际 · Anthropic",
    ),
    # —— 国际 · Google Gemini（LiteLLM，多需 GEMINI_API_KEY / GOOGLE_API_KEY）——
    LlmPreset(
        "gemini:gemini-2.0-flash",
        "litellm",
        "gemini/gemini-2.0-flash",
        "Google · gemini-2.0-flash",
        "国际 · Google Gemini",
    ),
    LlmPreset(
        "gemini:gemini-1.5-pro",
        "litellm",
        "gemini/gemini-1.5-pro",
        "Google · gemini-1.5-pro",
        "国际 · Google Gemini",
    ),
    LlmPreset(
        "gemini:gemini-1.5-flash",
        "litellm",
        "gemini/gemini-1.5-flash",
        "Google · gemini-1.5-flash",
        "国际 · Google Gemini",
    ),
)

_PRESET_MAP = {p.id: p for p in _LL_PRESETS}


def get_preset(preset_id: str) -> Optional[LlmPreset]:
    if not preset_id or not isinstance(preset_id, str):
        return None
    return _PRESET_MAP.get(preset_id.strip())


def list_presets_public() -> list[dict[str, Any]]:
    """供前端下拉；不含密钥。"""
    return [
        {
            "id": p.id,
            "provider": p.provider,
            "api_model": p.api_model,
            "label": p.label_zh,
            "group": p.group,
        }
        for p in _LL_PRESETS
    ]
