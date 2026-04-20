"""测试用户提供的 LLM Key 是否可用（极小请求；Kimi 走 Moonshot，其它经 LiteLLM 或通义）。"""

from __future__ import annotations

from typing import Optional

from app.services.llm_litellm import completion_text, custom_endpoint_completion, probe_custom_completion_backend


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


def ping_custom_endpoint(
    base_url: str,
    api_model: str,
    api_key: str,
    *,
    completion_backend: Optional[str] = None,
) -> str:
    """
    自定义 Base + 模型 + Key。
    未传 completion_backend 时先探测（Anthropic 再 LiteLLM）并返回 anthropic | litellm；
    已传则按该后端做一次极小请求校验。
    """
    from app.services.llm_custom_url import validate_custom_base_url

    key = (api_key or "").strip()
    if not key:
        raise ValueError("empty api key")
    model = (api_model or "").strip()
    if not model:
        raise ValueError("empty model")
    validated = validate_custom_base_url(base_url)
    bk = (completion_backend or "").strip().lower() or None
    if bk:
        custom_endpoint_completion(
            key,
            validated,
            model,
            "Reply with exactly: OK",
            max_tokens=32,
            temperature=0.0,
            timeout=60.0,
            completion_backend=bk,
        )
        return bk
    return probe_custom_completion_backend(key, validated, model, timeout=60.0)
