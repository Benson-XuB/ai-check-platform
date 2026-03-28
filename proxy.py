#!/usr/bin/env python3
"""Flask 代理：转发 Gitee/Kimi API 请求，解决 CORS。"""

import re
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".")
GITEE_API = "https://gitee.com/api/v5"


@app.after_request
def cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


def json_response(ok: bool, data=None, error: str = None):
    return jsonify({"ok": ok, "data": data, "error": error})


def parse_pr_url(url: str):
    """从 PR 链接解析 owner, repo, number。"""
    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", url.strip())
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2), "number": m.group(3)}


@app.route("/api/gitee/fetch-pr", methods=["POST"])
def fetch_pr():
    data = request.get_json() or {}
    pr_url = data.get("pr_url")
    token = data.get("gitee_token")
    if not pr_url or not token:
        return json_response(False, error="缺少 pr_url 或 gitee_token")
    parsed = parse_pr_url(pr_url)
    if not parsed:
        return json_response(False, error="PR 链接格式无效")
    owner, repo, number = parsed["owner"], parsed["repo"], parsed["number"]
    headers = {"Authorization": f"token {token}"}
    r = requests.get(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}",
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        return json_response(False, error=f"Gitee API 错误: {r.status_code} {r.text[:200]}")
    pr = r.json()
    r2 = requests.get(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/files",
        headers=headers,
        timeout=30,
    )
    files = r2.json() if r2.status_code == 200 else []
    diff_parts = []
    for f in files:
        diff_parts.append(f"--- {f.get('filename', '')}\n+++ {f.get('filename', '')}")
        diff_parts.append(f.get("patch", "") or "(binary or empty)")
    diff_text = "\n".join(diff_parts) if diff_parts else pr.get("body", "") or ""
    return json_response(True, data={
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "diff": diff_text,
        "owner": owner,
        "repo": repo,
        "number": number,
    })


KIMI_API = "https://api.moonshot.cn/v1"


@app.route("/api/kimi/review", methods=["POST"])
def kimi_review():
    data = request.get_json() or {}
    diff = data.get("diff", "")
    api_key = data.get("kimi_api_key")
    if not diff or not api_key:
        return json_response(False, error="缺少 diff 或 kimi_api_key")
    prompt = f"""请对以下代码 diff 进行代码审查。按以下格式返回，每个文件用 ## 文件: 路径 开头，每个评论用 ### 第 N 行 和 - [类型] 内容：

## 文件: path/to/file
### 第 12 行
- [建议] 具体建议
### 第 45 行
- [问题] 具体问题

如果 diff 为空或无法审查，请返回「暂无有效改动可审查」。

代码 diff：
```
{diff[:30000]}
```
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "moonshot-v1-32k",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    r = requests.post(
        f"{KIMI_API}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120,
    )
    if r.status_code != 200:
        return json_response(False, error=f"Kimi API 错误: {r.status_code} {r.text[:300]}")
    out = r.json()
    content = out.get("choices", [{}])[0].get("message", {}).get("content", "")
    return json_response(True, data={"review": content})


@app.route("/api/gitee/post-comment", methods=["POST"])
def post_comment():
    data = request.get_json() or {}
    owner = data.get("owner")
    repo = data.get("repo")
    number = data.get("number")
    comment = data.get("comment", "").strip()
    token = data.get("gitee_token")
    if not all([owner, repo, number, comment, token]):
        return json_response(False, error="缺少 owner/repo/number/comment/token")
    headers = {"Authorization": f"token {token}"}
    r = requests.post(
        f"{GITEE_API}/repos/{owner}/{repo}/pulls/{number}/comments",
        headers=headers,
        json={"body": comment},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        return json_response(False, error=f"Gitee API 错误: {r.status_code} {r.text[:200]}")
    return json_response(True, data={"posted": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
