"""
LLM 单次补全：
- Kimi / Moonshot：官方 OpenAI 兼容接口（openai SDK + MOONSHOT_BASE_URL）
- 通义 DashScope：LiteLLM「dashscope/模型名」
- 其它（OpenAI / Anthropic / Gemini / DeepSeek / 智谱等）：LiteLLM 完整 model 字符串，provider 固定为 litellm

模型 ID 需与 LiteLLM 文档一致：https://docs.litellm.ai/docs/providers
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import litellm

    litellm.set_verbose = False
except Exception:  # pragma: no cover
    litellm = None  # type: ignore


def _moonshot_chat(
    api_key: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
) -> str:
    from openai import OpenAI

    base = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/")
    client = OpenAI(api_key=(api_key or "").strip(), base_url=base, timeout=timeout)
    kwargs: dict[str, Any] = {
        "model": (api_model or "").strip(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.warning("moonshot chat failed model=%s: %s", api_model, e)
        raise RuntimeError(f"Kimi API 调用失败: {e}") from e
    if not resp.choices:
        return ""
    c = resp.choices[0].message.content
    return (c or "") if isinstance(c, str) else str(c or "")


def _extract_message_text(resp: Any) -> str:
    if not resp or not getattr(resp, "choices", None):
        return ""
    ch = resp.choices[0]
    msg = getattr(ch, "message", None)
    content: Any = getattr(msg, "content", None) if msg is not None else None
    if content is None and isinstance(ch, dict):
        content = (ch.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts)
    return str(content or "")


def litellm_completion(
    model: str,
    api_key: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.3,
    timeout: float = 120.0,
    base_url: Optional[str] = None,
) -> str:
    """调用 LiteLLM；model 为完整 ID，如 openai/gpt-4o、anthropic/claude-3-5-sonnet-20241022。"""
    if litellm is None:
        raise RuntimeError("litellm 未安装")
    kw: dict[str, Any] = {
        "model": (model or "").strip(),
        "messages": [{"role": "user", "content": prompt}],
        "api_key": (api_key or "").strip(),
        "temperature": temperature,
        "timeout": timeout,
    }
    if max_tokens is not None:
        kw["max_tokens"] = max_tokens
    if base_url:
        kw["base_url"] = base_url.rstrip("/")
    try:
        resp = litellm.completion(**kw)
    except Exception as e:
        logger.warning("litellm completion failed model=%s: %s", model, e)
        raise RuntimeError(f"LLM 调用失败: {e}") from e
    return _extract_message_text(resp)


def completion_text(
    provider: str,
    api_key: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.3,
    timeout: float = 120.0,
) -> str:
    """
    按站内 provider 分发：
    - kimi → Moonshot OpenAI 兼容
    - dashscope → LiteLLM dashscope/{api_model}
    - litellm → LiteLLM，api_model 已为完整字符串（如 openai/gpt-4o）
    """
    p = (provider or "").strip().lower()
    m = (api_model or "").strip()
    if not m:
        raise ValueError("empty api_model")

    if p == "kimi":
        return _moonshot_chat(
            api_key, m, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout
        )
    if p == "litellm":
        return litellm_completion(
            m, api_key, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout
        )
    if p == "dashscope":
        dash = f"dashscope/{m}"
        extra_base = os.getenv("DASHSCOPE_API_BASE", "").strip()
        return litellm_completion(
            dash,
            api_key,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            base_url=extra_base or None,
        )
    raise ValueError(f"unsupported provider: {provider}")
