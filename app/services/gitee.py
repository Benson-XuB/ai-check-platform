"""Gitee API 封装：拉取 PR、diff、文件内容，下发评论。"""

import base64
import re
from typing import List, Optional

import requests

GITEE_API = "https://gitee.com/api/v5"


def parse_pr_url(url: str) -> Optional[dict]:
    """从 PR 链接解析 owner, repo, number。"""
    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", url.strip())
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2), "number": m.group(3)}


def fetch_pr(pr_url: str, token: str) -> dict:
    """
    拉取 PR 信息、diff、变更文件完整内容。
    返回: {ok, data: {title, body, diff, owner, repo, number, head_sha, file_contexts}}
    """
    parsed = parse_pr_url(pr_url)
    if not parsed:
        return {"ok": False, "error": "PR 链接格式无效"}
    owner, repo, number = parsed["owner"], parsed["repo"], parsed["number"]
    headers = {"Authorization": f"token {token}"}

    # 拉取 PR 详情
    r = requests.get(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}",
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        return {"ok": False, "error": f"Gitee API 错误: {r.status_code} {r.text[:200]}"}
    pr = r.json()

    # 拉取 PR 变更文件
    r2 = requests.get(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/files",
        headers=headers,
        timeout=30,
    )
    files = r2.json() if r2.status_code == 200 else []

    # 组装 diff
    diff_parts = []
    for f in files:
        fn = f.get("filename", "") or ""
        fn = fn if isinstance(fn, str) else str(fn)
        diff_parts.append(f"--- {fn}\n+++ {fn}")
        patch = f.get("patch") or "(binary or empty)"
        diff_parts.append(patch if isinstance(patch, str) else "(binary or empty)")
    diff_text = "\n".join(diff_parts) if diff_parts else pr.get("body", "") or ""

    # 尝试原始 diff（便于 position 计算）
    head_sha = pr.get("head", {}).get("sha") if isinstance(pr.get("head"), dict) else None
    if not head_sha:
        r3 = requests.get(
            f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/commits",
            headers=headers,
            timeout=30,
        )
        if r3.status_code == 200 and r3.json():
            head_sha = r3.json()[0].get("sha")

    diff_url = pr.get("diff_url") or (pr.get("diff_urls", {}) or {}).get("html")
    if not diff_url and head_sha:
        diff_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/pulls/{number}.diff"
    if diff_url:
        try:
            r_diff = requests.get(diff_url, headers=headers, timeout=30)
            if r_diff.status_code == 200 and r_diff.text:
                diff_text = r_diff.text
        except Exception:
            pass

    # L2: 拉取每个变更文件的完整内容 (head_sha 版本)
    file_contexts: dict[str, str] = {}
    if head_sha:
        for f in files:
            fn = f.get("filename", "") or ""
            if not fn or not isinstance(fn, str):
                continue
            # 跳过二进制、大文件、依赖目录
            if any(x in fn for x in ["node_modules/", "vendor/", ".git/", "__pycache__/"]):
                continue
            if f.get("status") == "removed":
                continue
            try:
                rc = requests.get(
                    f"{GITEE_API}/repos/{owner}/{repo}/contents/{fn}",
                    headers=headers,
                    params={"ref": head_sha},
                    timeout=15,
                )
                if rc.status_code == 200:
                    data = rc.json()
                    if isinstance(data, dict) and data.get("content"):
                        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                        if len(raw) < 50000:  # 单文件 50KB 上限
                            file_contexts[fn] = raw
            except Exception:
                pass

    return {
        "ok": True,
        "data": {
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "diff": diff_text,
            "owner": owner,
            "repo": repo,
            "number": number,
            "head_sha": head_sha or "",
            "file_contexts": file_contexts,
            "changed_files": [f.get("filename", "") for f in files if f.get("filename")],
            "platform": "gitee",
        },
    }


def get_repo_tree_paths(owner: str, repo: str, ref: str, token: str) -> List[str]:
    """获取仓库在 ref 下的文件路径列表（用于 import 解析）。Gitee 无 tree API 时返回空列表。"""
    paths: List[str] = []
    headers = {"Authorization": f"token {token}"}
    try:
        # 尝试 Gitee trees API（若存在）
        r = requests.get(
            f"{GITEE_API}/repos/{owner}/{repo}/git/trees/{ref}",
            headers=headers,
            params={"recursive": "1"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and "tree" in data:
                for node in data.get("tree") or []:
                    if isinstance(node, dict) and node.get("type") == "blob":
                        p = node.get("path")
                        if p and isinstance(p, str):
                            paths.append(p)
    except Exception:
        pass
    return paths


def fetch_file_content(owner: str, repo: str, path: str, ref: str, token: str) -> Optional[str]:
    """拉取仓库指定路径在 ref 下的文件内容，不存在或非文件返回 None。"""
    if not path or not ref:
        return None
    headers = {"Authorization": f"token {token}"}
    try:
        r = requests.get(
            f"{GITEE_API}/repos/{owner}/{repo}/contents/{path}",
            headers=headers,
            params={"ref": ref},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict) or "content" not in data:
            return None
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return raw if len(raw) < 50000 else raw[:50000]
    except Exception:
        return None


def compute_diff_position(diff: str, path: str, target_line: int) -> Optional[int]:
    """根据 diff、文件路径、目标行号，计算 Gitee 所需的 position（diff 中的行数）。"""
    lines = diff.split("\n")
    cur_file = ""
    new_line = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for i, line in enumerate(lines):
        if line.startswith("--- ") or line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("a/") or p.startswith("b/"):
                p = p[2:]
            if line.startswith("+++ "):
                cur_file = p or cur_file
        elif line.startswith("@@"):
            m = hunk_re.match(line)
            if m:
                new_line = int(m.group(1))
        elif cur_file and (cur_file == path or cur_file.endswith("/" + path)):
            if line.startswith(" ") or line.startswith("+"):
                if new_line == target_line:
                    return i + 1
                new_line += 1
            elif line.startswith("-"):
                pass
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
    """下发评论到 PR，支持行级评论。"""
    if not all([owner, repo, number, comment, token]):
        return {"ok": False, "error": "缺少 owner/repo/number/comment/token"}
    headers = {"Authorization": f"token {token}"}
    payload: dict = {"body": comment}
    if path and line is not None and commit_id and diff:
        position = compute_diff_position(diff, path, line)
        if position is not None:
            payload["path"] = path
            payload["position"] = position
            payload["commit_id"] = commit_id
        else:
            payload["path"] = path
            payload["position"] = line
            payload["commit_id"] = commit_id
    if path and line is not None and "path" not in payload:
        payload["body"] = f"**[{path} 第 {line} 行]**\n\n{comment}"

    r = requests.post(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/comments",
        headers={**headers, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 201) and "path" in payload:
        send_data = {k: str(v) for k, v in payload.items()}
        r = requests.post(
            f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=headers,
            data=send_data,
            timeout=30,
        )
    if r.status_code not in (200, 201) and "path" in payload:
        payload_fb = {"body": payload.get("body", comment)}
        r2 = requests.post(
            f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=headers,
            json=payload_fb,
            timeout=30,
        )
        if r2.status_code in (200, 201):
            return {"ok": True}
        return {"ok": False, "error": f"Gitee API 错误: {r2.status_code} {r2.text[:200]}"}
    if r.status_code not in (200, 201):
        return {"ok": False, "error": f"Gitee API 错误: {r.status_code} {r.text[:200]}"}
    return {"ok": True}
