"""Prelaunch LLM：同一模型、不同 system prompt（兼容 Kimi / 通义）。"""

import json
from typing import Any, Dict, Optional

import httpx

from app.services.llm_defaults import get_public_default_llm_provider
from app.services.review import _call_dashscope


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    for start in ("{", "```json", "```"):
        i = text.find(start)
        if i < 0:
            continue
        chunk = text[i:].replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    return None


def llm_chat(
    system_prompt: str,
    user_content: str,
    provider: str,
    api_key: str,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.15,
) -> str:
    """返回模型原始文本（期望为 JSON）。"""
    p = (provider or get_public_default_llm_provider()).lower().strip()
    if p == "dashscope":
        combined = f"{system_prompt}\n\n---\n\n{user_content}"
        return _call_dashscope(
            api_key,
            "qwen-plus",
            combined,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    with httpx.Client(timeout=180) as client:
        r = client.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "moonshot-v1-32k",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Kimi API: {r.status_code} {r.text[:400]}")
    return r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""


def llm_chat_json(
    system_prompt: str,
    user_content: str,
    provider: str,
    api_key: str,
    *,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    raw = llm_chat(system_prompt, user_content, provider, api_key, max_tokens=max_tokens)
    obj = extract_json_object(raw)
    if obj is None:
        return {"_parse_error": True, "_raw": raw[:4000]}
    return obj
