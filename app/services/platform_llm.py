"""站点级默认 LLM（环境变量），供 SaaS 回退与用户未配置时使用。"""

from __future__ import annotations

import os

from app.services.llm_defaults import get_public_default_llm_provider


def platform_llm_key() -> tuple[str, str]:
    """返回 (provider, api_key)。"""
    provider = get_public_default_llm_provider()
    if provider == "kimi":
        key = (os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or "").strip()
    else:
        provider = "dashscope"
        key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    return provider, key
