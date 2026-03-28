"""站点级默认 LLM 厂商（通义 / Kimi）；由环境变量决定，用户页面无需选手动模型。"""

import os
from typing import Literal

SupportedProvider = Literal["dashscope", "kimi"]


def get_public_default_llm_provider() -> str:
    """
    自建部署时设置 PUBLIC_DEFAULT_LLM_PROVIDER=kimi 可全站默认 Kimi；
    未设置时默认 dashscope（通义千问）。
    """
    raw = (os.getenv("PUBLIC_DEFAULT_LLM_PROVIDER", "dashscope") or "dashscope").strip().lower()
    if raw in ("dashscope", "qwen", "tongyi", "通义"):
        return "dashscope"
    if raw in ("kimi", "moonshot"):
        return "kimi"
    return "dashscope"
