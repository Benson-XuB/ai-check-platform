"""GitHub REST API：拉取 PR、文件内容、行级 review comment（与 Gitee 返回结构对齐）。"""

import base64
import os
import re
from typing import List, Optional

import requests

GITHUB_API = os.getenv("GITHUB_API_BASE", "https://api.github.com").rstrip("/")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def parse_pr_url(url: str) -> Optional[dict]:
    u = url.strip().rstrip("/")
    u = re.sub(r"/(files|commits)(/.*)?$", "", u, flags=re.I)
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", u, re.I)
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2), "number": m.group(3)}


def _contents_url(owner: str, repo: str, path: str) -> str:
    from urllib.parse import quote

    enc = quote(path, safe="")
    return f"{GITHUB_API}/repos/{owner}/{repo}/contents/{enc}"


def fetch_pr(pr_url: str, token: str) -> dict:
    parsed = parse_pr_url(pr_url)
    if not parsed:
        return {"ok": False, "error": "GitHub PR 链接格式无效（示例：https://github.com/owner/repo/pull/1）"}
    owner, repo, number = parsed["owner"], parsed["repo"], parsed["number"]
    hdr = _headers(token)

    r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}", headers=hdr, timeout=30)
    if r.status_code != 200:
        return {"ok": False, "error": f"GitHub API 错误: {r.status_code} {r.text[:200]}"}
    pr = r.json()

    r_diff = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
        headers={**hdr, "Accept": "application/vnd.github.diff"},
        timeout=60,
    )
    diff_text = r_diff.text if r_diff.status_code == 200 and r_diff.text else ""

    files: List[dict] = []
    page = 1
    while True:
        rf = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/files",
            headers=hdr,
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        if rf.status_code != 200:
            break
        batch = rf.json()
        if not isinstance(batch, list) or not batch:
            break
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    if not diff_text and files:
        diff_parts = []
        for f in files:
            fn = f.get("filename", "") or ""
            if not isinstance(fn, str):
                fn = str(fn)
            diff_parts.append(f"--- {fn}\n+++ {fn}")
            patch = f.get("patch") or "(binary or empty)"
            diff_parts.append(patch if isinstance(patch, str) else "(binary or empty)")
        diff_text = "\n".join(diff_parts)

    head_sha = ""
    if isinstance(pr.get("head"), dict):
        head_sha = pr["head"].get("sha") or ""

    file_contexts: dict = {}
    if head_sha:
        for f in files:
            fn = f.get("filename", "") or ""
            if not fn or not isinstance(fn, str):
                continue
            if any(x in fn for x in ("node_modules/", "vendor/", ".git/", "__pycache__/")):
                continue
            if f.get("status") == "removed":
                continue
            if f.get("status") == "added" and not f.get("patch"):
                continue
            try:
                rc = requests.get(
                    _contents_url(owner, repo, fn),
                    headers=hdr,
                    params={"ref": head_sha},
                    timeout=15,
                )
                if rc.status_code != 200:
                    continue
                data = rc.json()
                if isinstance(data, dict) and data.get("content") and data.get("encoding") == "base64":
                    raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                    if len(raw) < 50000:
                        file_contexts[fn] = raw
            except Exception:
                pass

    return {
        "ok": True,
        "data": {
            "title": pr.get("title", "") or "",
            "body": pr.get("body", "") or "",
            "diff": diff_text,
            "owner": owner,
            "repo": repo,
            "number": number,
            "head_sha": head_sha,
            "file_contexts": file_contexts,
            "changed_files": [f.get("filename", "") for f in files if f.get("filename")],
            "platform": "github",
        },
    }


def get_repo_tree_paths(owner: str, repo: str, ref: str, token: str) -> List[str]:
    paths: List[str] = []
    hdr = _headers(token)
    try:
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}",
            headers=hdr,
            params={"recursive": "1"},
            timeout=20,
        )
        if r.status_code != 200:
            return paths
        data = r.json()
        for node in data.get("tree") or []:
            if isinstance(node, dict) and node.get("type") == "blob":
                p = node.get("path")
                if p and isinstance(p, str):
                    paths.append(p)
    except Exception:
        pass
    return paths


def fetch_file_content(owner: str, repo: str, path: str, ref: str, token: str) -> Optional[str]:
    if not path or not ref:
        return None
    hdr = _headers(token)
    try:
        r = requests.get(
            _contents_url(owner, repo, path),
            headers=hdr,
            params={"ref": ref},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return raw if len(raw) < 50000 else raw[:50000]
    except Exception:
        return None


def post_comment(
    owner: str,
    repo: str,
    number: str,
    comment: str,
    token: str,
    *,
    path: str = "",
    line: Optional[int] = None,
    commit_id: str = "",
    diff: str = "",
) -> dict:
    """GitHub：优先发 pull review comment（行级），失败则回落为 PR 讨论串评论。"""
    _ = diff  # GitHub 使用 line + side，不依赖 Gitee 的 position 计算
    if not all([owner, repo, number, comment, token]):
        return {"ok": False, "error": "缺少 owner/repo/number/comment/token"}
    hdr = _headers(token)
    hdr_json = {**hdr, "Content-Type": "application/json"}

    if path and line is not None and commit_id:
        payload = {
            "body": comment,
            "commit_id": commit_id,
            "path": path,
            "line": int(line),
            "side": "RIGHT",
        }
        r = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=hdr_json,
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            return {"ok": True}

    body = comment
    if path and line is not None:
        body = f"**[{path}:{line}]**\n\n{comment}"
    r2 = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments",
        headers=hdr_json,
        json={"body": body},
        timeout=30,
    )
    if r2.status_code in (200, 201):
        return {"ok": True}
    return {"ok": False, "error": f"GitHub API 错误: {r2.status_code} {r2.text[:200]}"}
