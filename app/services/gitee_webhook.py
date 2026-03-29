"""Gitee WebHook：签名校验与合并请求自动审查任务。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def verify_gitee_webhook(headers: Mapping[str, str], secret: str) -> bool:
    """
    校验 Gitee WebHook 请求。
    - 明文密码：X-Gitee-Token 与密钥完全一致。
    - 签名密钥：X-Gitee-Timestamp（毫秒）+ '\\n' + 密钥 → HMAC-SHA256 → Base64 → URL 编码，
      与 X-Gitee-Token 比对（兼容未编码的 Base64）。

    secret 为空时不校验（仅适用本地调试），并打 warning。
    """
    if not secret:
        logger.warning("GITEE_WEBHOOK_SECRET 未设置，Webhook 未校验来源（请勿用于公网）")
        return True
    token = (headers.get("x-gitee-token") or headers.get("X-Gitee-Token") or "").strip()
    if not token:
        logger.warning("Webhook 缺少 X-Gitee-Token")
        return False
    if hmac.compare_digest(token, secret):
        return True
    ts_raw = headers.get("x-gitee-timestamp") or headers.get("X-Gitee-Timestamp")
    if not ts_raw:
        logger.warning("签名校验需要 X-Gitee-Timestamp")
        return False
    try:
        ts = int(str(ts_raw).strip())
    except ValueError:
        return False
    if abs(int(time.time() * 1000) - ts) > 3600 * 1000:
        logger.warning("Webhook 时间戳偏移超过 1 小时")
        return False
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    b64 = base64.b64encode(digest).decode("ascii")
    candidates = {b64, urllib.parse.quote_plus(b64)}
    if token in candidates:
        return True
    try:
        unquoted = urllib.parse.unquote_plus(token)
        if hmac.compare_digest(unquoted, b64):
            return True
    except Exception:
        pass
    return False


def _pr_url_from_payload(payload: dict[str, Any]) -> Optional[str]:
    pr = payload.get("pull_request") or {}
    html_url = pr.get("html_url")
    if html_url and isinstance(html_url, str):
        return html_url.strip()
    number = pr.get("number")
    repo = payload.get("repository") or {}
    path_ns = repo.get("path_with_namespace")
    if path_ns and number is not None:
        ns = str(path_ns).strip()
        if "/" in ns:
            return f"https://gitee.com/{ns}/pulls/{number}"
    return None


def should_handle_merge_request_webhook(payload: dict[str, Any]) -> bool:
    """是否为需处理的合并请求 Webhook（opened 且未关闭）。"""
    name = payload.get("hook_name")
    if name and name != "merge_request_hooks":
        return False
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return False
    if pr.get("state") != "open":
        return False
    return True


def process_merge_request_webhook(payload: dict[str, Any]) -> None:
    """后台执行：拉 PR → 默认审查 → 可选自动发帖到 Gitee。"""
    if not should_handle_merge_request_webhook(payload):
        logger.info("Webhook 已忽略（非 merge_request_hooks 或 PR 非 open）: hook_name=%s", payload.get("hook_name"))
        return

    from app.routers.gitee import FetchPRRequest, run_fetch_pr
    from app.routers.review import ReviewRequest, run_review_core

    pr_url = _pr_url_from_payload(payload)
    if not pr_url:
        logger.error("Webhook 无法解析 PR 链接: %s", json.dumps(payload, ensure_ascii=False)[:500])
        return

    gitee_token = os.getenv("GITEE_TOKEN", "").strip()
    if not gitee_token:
        logger.error("Webhook 需要环境变量 GITEE_TOKEN")
        return

    provider = os.getenv("WEBHOOK_LLM_PROVIDER", "dashscope").strip().lower()
    if provider == "kimi":
        llm_key = (os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or "").strip()
    else:
        llm_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not llm_key:
        logger.error("Webhook 需要 LLM API Key（DASHSCOPE_API_KEY 或 KIMI_API_KEY）")
        return

    fetch_req = FetchPRRequest(
        pr_url=pr_url,
        gitee_token=gitee_token,
        enrich_context=_env_bool("WEBHOOK_ENRICH_CONTEXT"),
        use_symbol_graph=_env_bool("WEBHOOK_USE_SYMBOL_GRAPH"),
        use_treesitter=_env_bool("WEBHOOK_USE_TREESITTER"),
        use_pyright=_env_bool("WEBHOOK_USE_PYRIGHT"),
    )
    out = run_fetch_pr(fetch_req)
    if not out.get("ok"):
        logger.error("Webhook fetch PR 失败: %s", out.get("error"))
        return
    data = out["data"]

    use_default = provider == "dashscope" and _env_bool("WEBHOOK_USE_DEFAULT_REVIEW", default=True)
    try:
        default_passes = int(os.getenv("WEBHOOK_DEFAULT_PASSES", "8"))
    except ValueError:
        default_passes = 8
    review_req = ReviewRequest(
        diff=data.get("diff") or "",
        pr_title=data.get("title") or "",
        pr_body=data.get("body") or "",
        file_contexts=data.get("file_contexts") or {},
        llm_provider=provider,
        llm_api_key=llm_key,
        use_mock=False,
        use_default_review=use_default,
        default_passes=default_passes,
        use_semantic_context=_env_bool("WEBHOOK_USE_SEMANTIC_CONTEXT"),
        repo_key=f'{data.get("owner")}/{data.get("repo")}' if data.get("owner") and data.get("repo") else None,
        ref=data.get("head_sha") or None,
    )
    review_out = run_review_core(review_req)
    if not review_out.get("ok"):
        logger.error("Webhook 审查失败: %s", review_out.get("error"))
        return

    if not _env_bool("WEBHOOK_AUTO_POST_COMMENTS", default=True):
        logger.info("Webhook 审查完成，未发帖（WEBHOOK_AUTO_POST_COMMENTS=0）")
        return

    from app.services import gitee as gitee_svc

    comments = (review_out.get("data") or {}).get("comments") or []
    owner, repo, number = data.get("owner"), data.get("repo"), str(data.get("number") or "")
    head_sha = data.get("head_sha") or ""
    diff_text = data.get("diff") or ""
    posted = 0
    for item in comments:
        body = (item.get("suggestion") or item.get("content") or "").strip()
        if not body:
            continue
        path = item.get("file") or ""
        line = item.get("line")
        extra: dict[str, Any] = {}
        valid_path = path and path not in ("(整体)", "(未知文件)")
        valid_line = line is not None and line != ""
        line_int = None
        if valid_path and valid_line and head_sha:
            try:
                line_int = int(line) if isinstance(line, (int, float)) else int(str(line).strip())
            except (ValueError, TypeError):
                line_int = None
        if line_int is not None and valid_path and head_sha:
            extra["path"] = path
            extra["line"] = line_int
            extra["commit_id"] = head_sha
            extra["diff"] = diff_text
        r = gitee_svc.post_comment(
            owner,
            repo,
            number,
            body,
            gitee_token,
            path=extra.get("path", ""),
            line=extra.get("line"),
            commit_id=extra.get("commit_id", ""),
            diff=extra.get("diff", ""),
        )
        if r.get("ok"):
            posted += 1
        else:
            logger.warning("Webhook 发帖失败: %s", r.get("error"))
    logger.info("Webhook PR %s 审查完成，已发帖 %s/%s", pr_url, posted, len(comments))
