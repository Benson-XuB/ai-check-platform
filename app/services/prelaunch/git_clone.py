"""Git 克隆（HTTPS + Token）。"""

import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse


def redact_repo_url(url: str) -> str:
    """用于持久化展示，去掉可能嵌入的凭据。"""
    try:
        p = urlparse(url)
        if p.username or p.password:
            netloc = p.hostname or ""
            if p.port:
                netloc = f"{netloc}:{p.port}"
            return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        pass
    return url


def inject_git_token(url: str, token: Optional[str]) -> str:
    if not token:
        return url
    p = urlparse(url.strip())
    if p.scheme != "https":
        raise ValueError("MVP 仅支持 https:// 克隆，请使用 HTTPS URL")
    host = (p.hostname or "").lower()
    if "github.com" in host:
        user = "x-access-token"
    else:
        user = "oauth2"
    netloc = f"{user}:{token}@{p.hostname}"
    if p.port:
        netloc = f"{user}:{token}@{p.hostname}:{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


def clone_repo(clone_url: str, dest: Path, ref: Optional[str], timeout_sec: int = 600) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"clone 目标已存在: {dest}")
    cmd = ["git", "clone", "--depth", "1", clone_url, str(dest)]
    if ref:
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, clone_url, str(dest)]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[:2000]
        raise RuntimeError(f"git clone 失败 (exit {r.returncode}): {err}")
