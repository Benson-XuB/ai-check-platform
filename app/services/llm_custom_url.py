"""自定义 LLM Base URL 校验：HTTPS、禁止解析到私网/回环、可选域名后缀白名单。"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import Optional
from urllib.parse import urlparse


def _allow_insecure_http() -> bool:
    return os.getenv("LLM_CUSTOM_ALLOW_INSECURE_HTTP", "").strip().lower() in ("1", "true", "yes")


def _host_allowlist() -> list[str]:
    """非空时，hostname 必须匹配其中任一条后缀（小写）。"""
    raw = os.getenv("LLM_CUSTOM_BASE_ALLOWLIST", "").strip()
    if not raw:
        return []
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _hostname_matches_allowlist(hostname: str, allow: list[str]) -> bool:
    h = hostname.lower().rstrip(".")
    for suf in allow:
        if h == suf or h.endswith("." + suf):
            return True
    return False


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True
    if addr.version == 6 and (
        ipaddress.IPv6Address("::1") == addr
        or addr in ipaddress.IPv6Network("fc00::/7")
        or addr in ipaddress.IPv6Network("fe80::/10")
    ):
        return True
    return False


def _resolve_host_blocks_private(hostname: str) -> Optional[str]:
    """若任一路由解析到禁止 IP，返回错误说明；否则 None。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as e:
        return f"无法解析主机名: {e}"
    seen: set[str] = set()
    for *_, sockaddr in infos:
        ip = sockaddr[0]
        if ip in seen:
            continue
        seen.add(ip)
        if _is_blocked_ip(ip):
            return f"禁止访问该地址（解析到受限 IP: {ip}）"
    return None


def validate_custom_base_url(raw: str) -> str:
    """
    返回规范化后的 URL 字符串（无尾斜杠，用于存储与比较）。
    失败时 raise ValueError。
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("base URL 不能为空")
    p = urlparse(s)
    if not p.scheme or not p.netloc:
        raise ValueError("base URL 格式无效（需要 https://主机/路径）")
    scheme = p.scheme.lower()
    if scheme == "http" and not _allow_insecure_http():
        raise ValueError("仅允许 HTTPS；开发环境可设 LLM_CUSTOM_ALLOW_INSECURE_HTTP=1 以允许 http")
    if scheme not in ("https", "http"):
        raise ValueError("仅支持 http(s) 协议")
    host = p.hostname
    if not host:
        raise ValueError("缺少主机名")
    allow = _host_allowlist()
    if allow and not _hostname_matches_allowlist(host, allow):
        raise ValueError(f"主机不在允许列表中（LLM_CUSTOM_BASE_ALLOWLIST）")

    err = _resolve_host_blocks_private(host)
    if err:
        raise ValueError(err)

    # 规范化：无 fragment，路径保留
    netloc = p.netloc
    path = (p.path or "").rstrip("/") or ""
    out = f"{scheme}://{netloc}{path}"
    return out.rstrip("/")


def normalize_openai_compatible_base(validated_base: str) -> str:
    """OpenAI 兼容 chat.completions：确保以 /v1 结尾。"""
    u = validated_base.rstrip("/")
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u


def is_kimi_coding_url(validated_base: str) -> bool:
    b = validated_base.lower()
    return "api.kimi.com" in b and "coding" in b
