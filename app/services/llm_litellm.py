"""
LLM 单次补全：
- Kimi 开放平台（moonshot-v1-* 等）：OpenAI 兼容；sk-kimi- 优先 coding 的 /v1，否则 .cn/.ai；401 轮换
- Kimi Code（kimi-for-coding）：Anthropic Messages API，base https://api.kimi.com/coding（与 Claude Code 文档一致）
- 通义 DashScope：LiteLLM dashscope/
- litellm：含 OpenAI、Gemini、Anthropic/Claude 等；Gemini 仅 Google 协议，无 Anthropic 线
- 用户自定义 Base（SaaS）：保存前探测 anthropic | litellm；审查时按库字段选用，LiteLLM 分支走 OpenAI 兼容 HTTP（不经 openai SDK）

模型 ID 需与 LiteLLM 文档一致：https://docs.litellm.ai/docs/providers
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import litellm

    litellm.set_verbose = False
except Exception:  # pragma: no cover
    litellm = None  # type: ignore

_KIMI_CODING_V1 = "https://api.kimi.com/coding/v1"
# Anthropic SDK：与 Claude Code / Kimi 文档一致，根路径无 /v1（SDK 会拼 Messages 路径）
_KIMI_CODING_ANTHROPIC_BASE = "https://api.kimi.com/coding"
_MOONSHOT_CN = "https://api.moonshot.cn/v1"
_MOONSHOT_AI = "https://api.moonshot.ai/v1"
_KIMI_CODE_KEY_PREFIX = "sk-kimi-"
# 仅这些模型走 Kimi Code 的 Anthropic 线（与 OpenAI /v1 二选一，此处统一用 Anthropic）
_KIMI_CODE_ANTHROPIC_MODELS = frozenset({"kimi-for-coding"})


def _use_kimi_code_anthropic(api_model: str) -> bool:
    return (api_model or "").strip() in _KIMI_CODE_ANTHROPIC_MODELS


def _anthropic_message_blocks_to_text(msg: Any) -> str:
    """Anthropic Message.content：text / thinking 等块拼成可读字符串。"""
    parts: list[str] = []
    for block in getattr(msg, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype is None and isinstance(block, dict):
            btype = block.get("type")
        if btype == "text":
            t = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
            if t:
                parts.append(str(t))
        elif btype == "thinking" or btype == "redacted_thinking":
            t = getattr(block, "thinking", None) or getattr(block, "text", None)
            if t:
                parts.append(str(t))
    return "".join(parts).strip()


def _anthropic_messages_chat(
    api_key: str,
    anthropic_base_url: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
) -> str:
    """任意 Anthropic Messages 兼容网关（含 Kimi Code）。"""
    import anthropic

    base = (anthropic_base_url or "").strip().rstrip("/")
    kwargs_client: dict[str, Any] = {
        "api_key": (api_key or "").strip(),
        "base_url": base,
        "timeout": timeout,
    }
    if _is_kimi_coding_base(base):
        ua = _kimi_coding_user_agent()
        if ua:
            kwargs_client["default_headers"] = {"User-Agent": ua}
    client = anthropic.Anthropic(**kwargs_client)
    mt = max_tokens if max_tokens is not None else 4096
    try:
        msg = client.messages.create(
            model=(api_model or "").strip(),
            max_tokens=max(1, int(mt)),
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
    except Exception as e:
        logger.warning("anthropic messages failed base=%s model=%s: %s", base, api_model, e)
        raise RuntimeError(f"Anthropic API 调用失败: {e}") from e
    return _anthropic_message_blocks_to_text(msg) or ""


def _kimi_code_anthropic_chat(
    api_key: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
) -> str:
    """Kimi Code：Anthropic 兼容网关（官方与 Claude Code 同路径）。"""
    return _anthropic_messages_chat(
        api_key,
        _KIMI_CODING_ANTHROPIC_BASE,
        api_model,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


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


def _custom_auth_like_error(exc: BaseException) -> bool:
    """401/403 等不重试另一协议。"""
    if _is_moonshot_unauthorized(exc):
        return True
    code = getattr(exc, "status_code", None)
    if code in (401, 403):
        return True
    msg = str(exc).lower()
    if "401" in msg or "403" in msg:
        if "unauthorized" in msg or "forbidden" in msg or "invalid" in msg or "permission" in msg:
            return True
    return False


def _exception_or_cause_auth_like(exc: BaseException) -> bool:
    if _custom_auth_like_error(exc):
        return True
    c = getattr(exc, "__cause__", None)
    if c is not None and _custom_auth_like_error(c):
        return True
    return False


def _host_only_for_diag(url: str) -> str:
    try:
        p = urlparse((url or "").strip())
        if p.netloc:
            return p.netloc
        return ((url or "").strip()[:80] or "(空)")
    except Exception:
        return (url or "")[:80] or "(空)"


def _is_timeout_like_error(exc: BaseException) -> bool:
    """识别读超时/连接超时（含异常链上的 httpx 超时类型）。"""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        try:
            import httpx

            if isinstance(
                cur,
                (
                    httpx.ReadTimeout,
                    httpx.ConnectTimeout,
                    httpx.WriteTimeout,
                    httpx.PoolTimeout,
                    httpx.TimeoutException,
                ),
            ):
                return True
        except Exception:
            pass
        msg = str(cur).lower()
        if "timed out" in msg or "timeout" in msg or "read time out" in msg:
            return True
        nxt = getattr(cur, "__cause__", None)
        if nxt is None:
            nxt = getattr(cur, "__context__", None)
        cur = nxt
    return False


def _log_and_raise_custom_endpoint_error(
    backend: str,
    *,
    base_stored: str,
    litellm_effective_base: Optional[str],
    model: str,
    timeout_sec: float,
    exc: BaseException,
) -> None:
    """写日志并把可诊断信息拼进异常文案，便于报告页与日志对照。"""
    host = _host_only_for_diag(base_stored)
    timeout_like = _is_timeout_like_error(exc)
    bits = [
        f"调用方式={backend}",
        f"主机={host}",
        f"模型={model}",
        f"客户端读超时={int(timeout_sec)}s",
    ]
    if backend == "litellm" and litellm_effective_base:
        bits.append(f"LiteLLM基址={litellm_effective_base}")
    if timeout_like:
        bits.append("原因归类=读超时或连接超时(上游在限时内未返回完整响应)")
    diag = "[诊断] " + "；".join(bits)
    if timeout_like:
        logger.error("%s | 原始错误: %s", diag, exc, exc_info=True)
    else:
        logger.warning("%s | 原始错误: %s", diag, exc, exc_info=True)
    raise RuntimeError(f"自定义端点调用失败: {exc} | {diag}") from exc


def _custom_litellm_model_id(api_model: str) -> str:
    """自定义端点走 LiteLLM OpenAI 兼容时：openai/<deployment>；已带 openai/、azure/ 前缀则原样。"""
    m = (api_model or "").strip()
    if not m:
        raise ValueError("empty api_model")
    if m.startswith("openai/") or m.startswith("azure/"):
        return m
    return f"openai/{m}"


def probe_custom_completion_backend(
    api_key: str,
    base_url_validated: str,
    api_model: str,
    *,
    timeout: float = 45.0,
) -> str:
    """保存自定义凭证前：先 Anthropic（用户 base），再 LiteLLM OpenAI 兼容；返回 anthropic | litellm。"""
    from app.services.llm_custom_url import normalize_openai_compatible_base

    m = (api_model or "").strip()
    if not m:
        raise ValueError("empty api_model")
    base = (base_url_validated or "").strip().rstrip("/")
    ping = "Reply with exactly: OK"
    anthropic_err: Optional[BaseException] = None
    try:
        _anthropic_messages_chat(
            api_key,
            base,
            m,
            ping,
            max_tokens=16,
            temperature=0.0,
            timeout=timeout,
        )
        return "anthropic"
    except Exception as e:
        anthropic_err = e
        logger.info("custom credential anthropic probe failed, trying litellm: %s", e)
    try:
        ob = normalize_openai_compatible_base(base_url_validated)
        litellm_completion(
            _custom_litellm_model_id(m),
            api_key,
            ping,
            max_tokens=16,
            temperature=0.0,
            timeout=timeout,
            base_url=ob,
        )
        return "litellm"
    except Exception as e2:
        a = str(anthropic_err) if anthropic_err else ""
        raise RuntimeError(
            f"自定义端点不可用：Anthropic 与 LiteLLM 均失败。Anthropic: {a}; LiteLLM: {e2}"
        ) from e2


def custom_endpoint_completion(
    api_key: str,
    base_url_validated: str,
    api_model: str,
    prompt: str,
    *,
    max_tokens: Optional[int],
    temperature: float,
    timeout: float,
    completion_backend: Optional[str] = None,
) -> str:
    """
    用户自定义 HTTPS Base + 模型名。
    completion_backend 为 anthropic | litellm（来自库内探测结果）；未传时当次请求内先探测（多一次小请求，旧凭证兼容）。
    不再使用 OpenAI Python SDK；LiteLLM 分支走 OpenAI 兼容 HTTP。
    """
    from app.services.llm_custom_url import normalize_openai_compatible_base

    m = (api_model or "").strip()
    if not m:
        raise ValueError("empty api_model")
    base = (base_url_validated or "").strip().rstrip("/")

    bk_in = (completion_backend or "").strip().lower() or None
    backend = bk_in if bk_in in ("anthropic", "litellm") else None
    if backend is None:
        backend = probe_custom_completion_backend(
            api_key, base_url_validated, m, timeout=min(45.0, float(timeout))
        )

    if backend == "anthropic":
        try:
            return _anthropic_messages_chat(
                api_key,
                base,
                m,
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        except Exception as e:
            _log_and_raise_custom_endpoint_error(
                "anthropic",
                base_stored=base_url_validated,
                litellm_effective_base=None,
                model=m,
                timeout_sec=timeout,
                exc=e,
            )

    if backend == "litellm":
        ob = normalize_openai_compatible_base(base_url_validated)
        try:
            return litellm_completion(
                _custom_litellm_model_id(m),
                api_key,
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                base_url=ob,
            )
        except Exception as e:
            _log_and_raise_custom_endpoint_error(
                "litellm",
                base_stored=base_url_validated,
                litellm_effective_base=ob,
                model=m,
                timeout_sec=timeout,
                exc=e,
            )

    raise ValueError(f"unsupported custom completion_backend: {backend}")


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
        if _use_kimi_code_anthropic(m):
            return _kimi_code_anthropic_chat(
                api_key, m, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout
            )
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
    if p == "custom":
        raise ValueError("provider=custom 应使用 custom_endpoint_completion(base_url, …)，勿直接走 completion_text")
    raise ValueError(f"unsupported provider: {provider}")
