"""
LLM 单次补全：
- Kimi / Moonshot：官方 OpenAI 兼容接口（openai SDK + MOONSHOT_BASE_URL）
 - 密钥以 sk-kimi- 开头时优先 api.kimi.com/coding/v1，否则优先 Moonshot 开放平台（.cn/.ai 或 MOONSHOT_BASE_URL）
  - 鉴权类失败时在另一路重试，减少「密钥与域名不匹配」导致的 401
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

_KIMI_CODING_V1 = "https://api.kimi.com/coding/v1"
_MOONSHOT_CN = "https://api.moonshot.cn/v1"
_MOONSHOT_AI = "https://api.moonshot.ai/v1"
_KIMI_CODE_KEY_PREFIX = "sk-kimi-"


def _is_kimi_coding_base(base: str) -> bool:
    b = (base or "").strip().rstrip("/").lower()
    return "api.kimi.com/coding" in b


def _kimi_coding_user_agent() -> Optional[str]:
    """
    Kimi Code 接口可能对非 Agent 客户端返回 403；部分环境需带 Coding Agent 式 User-Agent。
    未设置 KIMI_CODING_USER_AGENT 时默认 claude-code/0.1.0；显式设为空字符串则不发送该头。
    """
    if "KIMI_CODING_USER_AGENT" not in os.environ:
        return "claude-code/0.1.0"
    v = os.environ["KIMI_CODING_USER_AGENT"].strip()
    return v or None


def _moonshot_assistant_text(message: Any) -> str:
    """content 与 reasoning_content（Kimi Code 思考模型可能只占后者）。"""
    if message is None:
        return ""
    content: Any = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    reasoning: Any = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    if isinstance(content, str):
        return content
    return str(content or "")


def _official_moonshot_bases() -> list[str]:
    """开放平台根路径序列（未设 MOONSHOT_BASE_URL 时为 .cn 再 .ai；与 .cn/.ai 一致时双端轮换）。"""
    raw = (os.getenv("MOONSHOT_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return [_MOONSHOT_CN.rstrip("/"), _MOONSHOT_AI.rstrip("/")]
    norm_cn = _MOONSHOT_CN.rstrip("/")
    norm_ai = _MOONSHOT_AI.rstrip("/")
    if raw == norm_cn:
        return [norm_cn, norm_ai]
    if raw == norm_ai:
        return [norm_ai, norm_cn]
    return [raw]


def _kimi_moonshot_endpoints_to_try(api_key: str) -> list[str]:
    """
    Kimi 调用顺序：sk-kimi- 优先 Kimi Code，否则优先开放平台；去重。
    """
    coding = _KIMI_CODING_V1.rstrip("/")
    official = _official_moonshot_bases()
    key = (api_key or "").strip()
    if key.startswith(_KIMI_CODE_KEY_PREFIX):
        merged = [coding] + official
    else:
        merged = official + [coding]
    seen: set[str] = set()
    out: list[str] = []
    for b in merged:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _is_moonshot_unauthorized(exc: BaseException) -> bool:
    try:
        from openai import AuthenticationError

        if isinstance(exc, AuthenticationError):
            return True
    except Exception:
        pass
    code = getattr(exc, "status_code", None)
    if code == 401:
        return True
    msg = str(exc).lower()
    return "401" in msg or "unauthorized" in msg or "invalid authentication" in msg


def _moonshot_chat_single(
    base: str,
    api_key: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
):
    from openai import OpenAI

    kwargs_client: dict[str, Any] = {
        "api_key": (api_key or "").strip(),
        "base_url": base.rstrip("/"),
        "timeout": timeout,
    }
    if _is_kimi_coding_base(base):
        ua = _kimi_coding_user_agent()
        if ua:
            kwargs_client["default_headers"] = {"User-Agent": ua}
    client = OpenAI(**kwargs_client)
    kwargs: dict[str, Any] = {
        "model": (api_model or "").strip(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return client.chat.completions.create(**kwargs)


def _moonshot_chat(
    api_key: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
) -> str:
    last: Optional[BaseException] = None
    bases = _kimi_moonshot_endpoints_to_try(api_key)
    for idx, base in enumerate(bases):
        try:
            resp = _moonshot_chat_single(
                base, api_key, api_model, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout
            )
        except Exception as e:
            last = e
            if _is_moonshot_unauthorized(e) and idx + 1 < len(bases):
                logger.info(
                    "kimi 鉴权失败 base=%s model=%s，尝试下一 base",
                    base,
                    api_model,
                )
                continue
            logger.warning("moonshot chat failed base=%s model=%s: %s", base, api_model, e)
            raise RuntimeError(f"Kimi API 调用失败: {e}") from e
        if not resp.choices:
            return ""
        return _moonshot_assistant_text(resp.choices[0].message)
    if last:
        raise RuntimeError(f"Kimi API 调用失败: {last}") from last
    raise RuntimeError("Kimi API 调用失败: 无可用端点")


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
