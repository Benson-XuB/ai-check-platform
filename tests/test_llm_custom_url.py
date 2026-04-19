"""自定义 LLM Base URL 校验与规范化（无网络：mock DNS）。"""

import socket

import pytest

from app.services.llm_custom_url import (
    is_kimi_coding_url,
    normalize_openai_compatible_base,
    validate_custom_base_url,
)


def _addrinfo_public_v4(*_a, **_k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]


def _addrinfo_loopback(*_a, **_k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def test_validate_empty():
    with pytest.raises(ValueError, match="不能为空"):
        validate_custom_base_url("")


def test_validate_invalid_format():
    with pytest.raises(ValueError, match="格式无效"):
        validate_custom_base_url("not-a-url")


def test_validate_https_rejects_http_without_flag(monkeypatch):
    monkeypatch.delenv("LLM_CUSTOM_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(ValueError, match="HTTPS"):
        validate_custom_base_url("http://api.example.com/v1")


def test_validate_http_allowed_when_flag_set(monkeypatch):
    monkeypatch.setenv("LLM_CUSTOM_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_public_v4)
    out = validate_custom_base_url("http://api.example.com/v1/")
    assert out == "http://api.example.com/v1"


def test_validate_https_public_ip_ok(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_public_v4)
    out = validate_custom_base_url("https://api.example.com")
    assert out == "https://api.example.com"
    out2 = validate_custom_base_url("https://api.example.com/foo/bar/")
    assert out2 == "https://api.example.com/foo/bar"


def test_validate_blocks_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_loopback)
    with pytest.raises(ValueError, match="受限 IP"):
        validate_custom_base_url("https://api.example.com")


def test_validate_allowlist_rejects_unknown_host(monkeypatch):
    monkeypatch.setenv("LLM_CUSTOM_BASE_ALLOWLIST", "api.kimi.com,trusted.org")
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_public_v4)
    with pytest.raises(ValueError, match="允许列表"):
        validate_custom_base_url("https://evil.com")


def test_validate_allowlist_accepts_suffix(monkeypatch):
    monkeypatch.setenv("LLM_CUSTOM_BASE_ALLOWLIST", "kimi.com")
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_public_v4)
    out = validate_custom_base_url("https://api.kimi.com/coding")
    assert "kimi.com" in out


def test_normalize_openai_compatible_base():
    assert normalize_openai_compatible_base("https://x.com") == "https://x.com/v1"
    assert normalize_openai_compatible_base("https://x.com/v1") == "https://x.com/v1"
    assert normalize_openai_compatible_base("https://x.com/v1/") == "https://x.com/v1"


def test_is_kimi_coding_url():
    assert is_kimi_coding_url("https://api.kimi.com/coding/v1") is True
    assert is_kimi_coding_url("https://API.KIMI.COM/foo/coding") is True
    assert is_kimi_coding_url("https://api.moonshot.cn/v1") is False
