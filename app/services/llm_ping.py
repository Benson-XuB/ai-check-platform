"""测试用户提供的 LLM Key 是否可用（极小请求；Kimi 走 Moonshot，其它经 LiteLLM 或通义）。"""

from __future__ import annotations

from app.services.llm_litellm import completion_text


def ping_preset(provider: str, api_model: str, api_key: str) -> None:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("empty api key")
    model = (api_model or "").strip()
    if not model:
        raise ValueError("empty model")
    p = (provider or "").strip().lower()
    if p not in ("dashscope", "kimi", "litellm"):
        raise ValueError(f"unsupported provider: {provider}")
    completion_text(
        p,
        key,
        model,
        "Reply with exactly: OK",
        max_tokens=8,
        temperature=0.0,
        timeout=60.0,
    )
