"""Gitee SaaS：OAuth、仓库 WebHook 注册、按用户写审查报告（平台 LLM Key）。"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx

from app.routers.gitee import FetchPRRequest, run_fetch_pr
from app.routers.review import ReviewRequest, run_review_core
from app.services import gitee_webhook as wh_svc
from app.services.llm_user_resolve import resolve_llm_for_review
from app.storage.db import create_db_engine
from app.storage.models import AppUser, GiteeOAuthAccount, GiteeWatchedRepo, PrReviewReport

logger = logging.getLogger(__name__)

GITEE_OAUTH_AUTHORIZE = "https://gitee.com/oauth/authorize"
GITEE_OAUTH_TOKEN = "https://gitee.com/oauth/token"
GITEE_API = "https://gitee.com/api/v5"

_DEFAULT_HTTP_TIMEOUT_SEC = 45
_DEFAULT_HTTP_RETRIES = 2  # total attempts = 1 + retries


def _http_timeout() -> httpx.Timeout:
    try:
        total = float(os.getenv("GITEE_HTTP_TIMEOUT_SEC", str(_DEFAULT_HTTP_TIMEOUT_SEC)))
    except ValueError:
        total = float(_DEFAULT_HTTP_TIMEOUT_SEC)
    total = max(5.0, min(120.0, total))
    # Do not cap connect below total: TLS handshake to gitee.com from overseas often needs the full budget.
    return httpx.Timeout(timeout=total, connect=total, read=total, write=total, pool=total)


def _gitee_http_client() -> httpx.Client:
    """HTTPS_PROXY / HTTP_PROXY respected (trust_env). Optional GITEE_HTTPS_PROXY overrides HTTPS only."""
    proxy = os.getenv("GITEE_HTTPS_PROXY", "").strip() or None
    return httpx.Client(timeout=_http_timeout(), trust_env=True, proxy=proxy)


def _http_retries() -> int:
    try:
        return max(0, min(3, int(os.getenv("GITEE_HTTP_RETRIES", str(_DEFAULT_HTTP_RETRIES)))))
    except ValueError:
        return _DEFAULT_HTTP_RETRIES


def _request_with_retry(fn, *, op: str) -> Any:
    retries = _http_retries()
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as e:
            last_err = e
            # small backoff
            if attempt < retries + 2:
                time.sleep(0.3 * attempt)
                continue
            break
    raise RuntimeError(
        f"{op} 网络超时/握手失败：{last_err}。"
        " 海外机房（如 Railway）访问 gitee.com 常出现 TLS 握手超时，与 OAuth 配置无关。"
        " 可尝试：增大 GITEE_HTTP_TIMEOUT_SEC（如 60）、GITEE_HTTP_RETRIES（如 3）；"
        "配置 HTTPS_PROXY 或在环境变量 GITEE_HTTPS_PROXY 指定可访问 Gitee 的 HTTPS 代理；"
        "或将本服务迁到国内/香港等出站更友好的环境；或改用 GitHub App。"
    ) from last_err


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def oauth_config_ok() -> bool:
    return bool(
        os.getenv("GITEE_OAUTH_CLIENT_ID", "").strip()
        and os.getenv("GITEE_OAUTH_CLIENT_SECRET", "").strip()
        and os.getenv("GITEE_OAUTH_REDIRECT_URI", "").strip()
    )


def public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def gitee_oauth_authorize_url(state: str) -> str:
    cid = os.getenv("GITEE_OAUTH_CLIENT_ID", "").strip()
    redir = os.getenv("GITEE_OAUTH_REDIRECT_URI", "").strip()
    scope = os.getenv(
        "GITEE_OAUTH_SCOPES",
        "user_info pull_requests projects hook",
    ).strip()
    q = urlencode(
        {
            "client_id": cid,
            "redirect_uri": redir,
            "response_type": "code",
            "scope": scope,
            "state": state,
        }
    )
    return f"{GITEE_OAUTH_AUTHORIZE}?{q}"


def exchange_code_for_token(code: str) -> dict[str, Any]:
    cid = os.getenv("GITEE_OAUTH_CLIENT_ID", "").strip()
    sec = os.getenv("GITEE_OAUTH_CLIENT_SECRET", "").strip()
    redir = os.getenv("GITEE_OAUTH_REDIRECT_URI", "").strip()
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.post(
                GITEE_OAUTH_TOKEN,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": cid,
                    "client_secret": sec,
                    "redirect_uri": redir,
                },
                headers={"Accept": "application/json"},
            ),
            op="Gitee OAuth 换 token",
        )
    if r.status_code != 200:
        logger.warning("Gitee token exchange failed: %s %s", r.status_code, r.text[:300])
        raise RuntimeError(f"Gitee OAuth 换 token 失败: HTTP {r.status_code}")
    return r.json()


def fetch_gitee_user(access_token: str) -> dict[str, Any]:
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.get(f"{GITEE_API}/user", params={"access_token": access_token}),
            op="读取 Gitee 用户",
        )
    if r.status_code != 200:
        raise RuntimeError(f"读取 Gitee 用户失败: HTTP {r.status_code}")
    return r.json()


def split_path_with_namespace(path_with_namespace: str) -> tuple[str, str]:
    parts = path_with_namespace.strip().strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"无效 path_with_namespace: {path_with_namespace}")
    repo = parts[-1]
    owner = "/".join(parts[:-1])
    return owner, repo


def list_user_repos(access_token: str, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.get(
                f"{GITEE_API}/user/repos",
                params={
                    "access_token": access_token,
                    "page": page,
                    "per_page": per_page,
                    "sort": "updated",
                },
            ),
            op="读取 Gitee 仓库列表",
        )
    if r.status_code != 200:
        logger.warning("list repos failed: %s %s", r.status_code, r.text[:200])
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def list_repo_hooks(access_token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    eo, er = quote(owner, safe=""), quote(repo, safe="")
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.get(
                f"{GITEE_API}/repos/{eo}/{er}/hooks",
                params={"access_token": access_token, "page": 1, "per_page": 50},
            ),
            op="读取 Gitee WebHook 列表",
        )
    if r.status_code != 200:
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def create_repo_hook(
    access_token: str,
    owner: str,
    repo: str,
    *,
    url: str,
    password: str,
) -> Optional[dict[str, Any]]:
    eo, er = quote(owner, safe=""), quote(repo, safe="")
    body = {
        "url": url,
        "password": password,
        "push_events": False,
        "tag_push_events": False,
        "issues_events": False,
        "note_events": False,
        "merge_requests_events": True,
    }
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.post(
                f"{GITEE_API}/repos/{eo}/{er}/hooks",
                params={"access_token": access_token},
                json=body,
            ),
            op="创建 Gitee WebHook",
        )
    if r.status_code not in (200, 201):
        logger.warning("create hook failed %s/%s: %s %s", owner, repo, r.status_code, r.text[:300])
        return None
    try:
        return r.json()
    except Exception:
        return None


def path_with_namespace_from_payload(payload: dict[str, Any]) -> Optional[str]:
    repo = payload.get("repository") or {}
    p = repo.get("path_with_namespace") or repo.get("full_name")
    if not p:
        return None
    s = str(p).strip()
    return s or None


def parse_gitee_pull_url(pr_url: str) -> tuple[str, int]:
    """解析 https://gitee.com/{path_with_namespace}/pulls/{number}。"""
    s = (pr_url or "").strip().rstrip("/")
    marker = "/pulls/"
    if marker not in s:
        raise ValueError("不是有效的 Gitee 合并请求链接（需含 /pulls/）")
    head, tail = s.split(marker, 1)
    num_part = tail.split("/")[0].strip()
    try:
        pr_number = int(num_part)
    except ValueError as e:
        raise ValueError("合并请求编号无效") from e
    if "gitee.com/" not in head.lower():
        raise ValueError("仅支持 gitee.com 链接")
    path_ns = head.split("gitee.com/", 1)[1].strip("/")
    if not path_ns or pr_number < 1:
        raise ValueError("无效链接")
    return path_ns, pr_number


def list_repo_open_pulls(
    access_token: str,
    path_with_namespace: str,
    *,
    page: int = 1,
    per_page: int = 50,
) -> list[dict[str, Any]]:
    """列出指定仓库 state=open 的合并请求（Gitee API v5）。"""
    owner, repo = split_path_with_namespace(path_with_namespace)
    eo, er = quote(owner, safe=""), quote(repo, safe="")
    per_page = max(1, min(int(per_page), 100))
    page = max(1, int(page))
    with _gitee_http_client() as client:
        r = _request_with_retry(
            lambda: client.get(
                f"{GITEE_API}/repos/{eo}/{er}/pulls",
                params={
                    "access_token": access_token,
                    "state": "open",
                    "page": page,
                    "per_page": per_page,
                    "sort": "updated",
                },
            ),
            op="列出 Gitee 打开的合并请求",
        )
    if r.status_code != 200:
        logger.warning(
            "list open pulls failed %s: %s %s",
            path_with_namespace,
            r.status_code,
            r.text[:200],
        )
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def run_saas_gitee_pr_review(
    app_user_id: int,
    gitee_access_token: str,
    *,
    pr_url: str,
    path_with_namespace: str,
    pr_number: int,
    pr_title: Optional[str] = None,
) -> None:
    """
    使用用户 OAuth token 拉取合并请求，按用户默认模型或平台兜底模型审查，写入 pr_review_reports；不向 Gitee 发帖。
    WebHook 与「首次接入手动审查」共用。
    """
    resolved = resolve_llm_for_review(app_user_id)
    provider, llm_key, api_model = resolved.provider, resolved.api_key, resolved.api_model
    if not llm_key:
        logger.error("SaaS 审查需要 LLM API Key（用户默认凭证与平台环境变量均无）")
        _save_report_failed(
            app_user_id,
            path_with_namespace,
            pr_number,
            None,
            pr_title,
            "未配置 LLM API Key（用户默认凭证与平台环境变量均无）",
        )
        return

    fetch_req = FetchPRRequest(
        pr_url=pr_url,
        vcs_token=gitee_access_token,
        enrich_context=_env_bool("SAAS_WEBHOOK_ENRICH_CONTEXT", default=False),
        use_symbol_graph=_env_bool("SAAS_WEBHOOK_USE_SYMBOL_GRAPH", default=False),
        use_treesitter=_env_bool("SAAS_WEBHOOK_USE_TREESITTER", default=False),
        use_pyright=_env_bool("SAAS_WEBHOOK_USE_PYRIGHT", default=False),
    )
    out = run_fetch_pr(fetch_req)
    if not out.get("ok"):
        logger.error("SaaS fetch PR 失败: %s", out.get("error"))
        _save_report_failed(
            app_user_id,
            path_with_namespace,
            pr_number,
            None,
            pr_title,
            str(out.get("error") or "fetch failed"),
        )
        return
    data = out["data"]
    head_sha = (data.get("head_sha") or "")[:80] or None
    title_use = pr_title if pr_title else None
    if not title_use and isinstance(data.get("title"), str):
        title_use = (data.get("title") or "")[:1024]

    use_default = (
        provider == "dashscope"
        and _env_bool("SAAS_WEBHOOK_USE_DEFAULT_REVIEW", default=True)
        and api_model is None
    )
    try:
        default_passes = int(os.getenv("SAAS_WEBHOOK_DEFAULT_PASSES", "8"))
    except ValueError:
        default_passes = 8

    review_req = ReviewRequest(
        diff=data.get("diff") or "",
        pr_title=data.get("title") or "",
        pr_body=data.get("body") or "",
        file_contexts=data.get("file_contexts") or {},
        llm_provider=provider,
        llm_api_key=llm_key,
        llm_model=api_model,
        llm_custom_base_url=resolved.custom_base_url,
        use_mock=False,
        use_default_review=use_default,
        default_passes=default_passes,
        use_semantic_context=_env_bool("SAAS_WEBHOOK_USE_SEMANTIC_CONTEXT", default=False),
        repo_key=f'{data.get("owner")}/{data.get("repo")}' if data.get("owner") and data.get("repo") else None,
        ref=data.get("head_sha") or None,
    )
    review_out = run_review_core(review_req)
    if not review_out.get("ok"):
        _save_report_failed(
            app_user_id,
            path_with_namespace,
            pr_number,
            head_sha,
            title_use,
            str(review_out.get("error") or "review failed"),
        )
        return

    payload_json = json.dumps(review_out.get("data") or {}, ensure_ascii=False)
    report_id = _save_report_ok(
        app_user_id, path_with_namespace, pr_number, head_sha, title_use, payload_json
    )
    logger.info(
        "SaaS 审查已写入报告 user=%s repo=%s pr=%s report_id=%s",
        app_user_id,
        path_with_namespace,
        pr_number,
        report_id,
    )
    if report_id is not None:
        _post_gitee_mr_review_link_comment(
            gitee_access_token=gitee_access_token,
            path_with_namespace=path_with_namespace,
            pr_number=pr_number,
            report_id=report_id,
        )


def _saas_gitee_post_review_link_enabled() -> bool:
    """默认在 MR 上发一条带报告页链接的评论（便于从 Gitee 跳转并触发站内通知）；设 SAAS_GITEE_POST_REVIEW_LINK=0 可关闭。"""
    raw = (os.getenv("SAAS_GITEE_POST_REVIEW_LINK") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _post_gitee_mr_review_link_comment(
    *,
    gitee_access_token: str,
    path_with_namespace: str,
    pr_number: int,
    report_id: int,
) -> None:
    if not _saas_gitee_post_review_link_enabled():
        return
    try:
        owner, repo = split_path_with_namespace(path_with_namespace)
    except ValueError:
        logger.warning("skip MR review link: bad path_with_namespace %s", path_with_namespace)
        return
    if pr_number < 1:
        return
    base = public_base_url().rstrip("/")
    report_url = f"{base}/app-gitee/reports?open={report_id}"
    body = (
        f"🤖 **AI 审查已完成**，[查看报告与详细意见]({report_url}) 。\n\n"
        "*（打开链接需已在审查平台使用同一 Gitee 账号登录。Gitee 可能会向关注此合并请求的用户发送评论通知。）*"
    )
    from app.services import gitee as gitee_svc

    res = gitee_svc.post_comment(
        owner, repo, str(pr_number), body, gitee_access_token
    )
    if not res.get("ok"):
        logger.warning(
            "Gitee MR 发布审查链接失败 %s PR#%s: %s",
            path_with_namespace,
            pr_number,
            res.get("error"),
        )
    else:
        logger.info(
            "Gitee MR 已发布审查链接 %s PR#%s report_id=%s",
            path_with_namespace,
            pr_number,
            report_id,
        )


def process_saas_merge_request_webhook(
    payload: dict[str, Any],
    *,
    app_user_id: int,
    gitee_access_token: str,
) -> None:
    """
    使用用户 OAuth token 拉 PR 并审查，写入 pr_review_reports；
    默认在 MR 上发一条含报告页链接的评论（可用 SAAS_GITEE_POST_REVIEW_LINK=0 关闭）。
    """
    if not wh_svc.should_handle_merge_request_webhook(payload):
        return

    pr_url = wh_svc._pr_url_from_payload(payload)
    if not pr_url:
        logger.error("SaaS Webhook 无法解析 PR 链接")
        return

    path_ns = path_with_namespace_from_payload(payload) or ""
    pr = payload.get("pull_request") or {}
    pr_number = pr.get("number")
    try:
        pr_number_int = int(pr_number) if pr_number is not None else 0
    except (TypeError, ValueError):
        pr_number_int = 0
    pr_title = (pr.get("title") or "")[:1024] if isinstance(pr.get("title"), str) else None

    run_saas_gitee_pr_review(
        app_user_id,
        gitee_access_token,
        pr_url=pr_url,
        path_with_namespace=path_ns,
        pr_number=pr_number_int,
        pr_title=pr_title,
    )


def run_saas_gitee_onboarding_review_for_url(
    app_user_id: int,
    gitee_access_token: str,
    pr_url: str,
) -> None:
    """首次接入手动审查：仅用户点击后调用；解析链接后走与 WebHook 相同的审查逻辑。"""
    try:
        path_ns, num = parse_gitee_pull_url(pr_url)
    except ValueError:
        logger.warning("onboarding review skipped, invalid url: %s", pr_url[:120])
        return
    run_saas_gitee_pr_review(
        app_user_id,
        gitee_access_token,
        pr_url=pr_url.strip(),
        path_with_namespace=path_ns,
        pr_number=num,
        pr_title=None,
    )


def _save_report_ok(
    user_id: int,
    path_ns: str,
    pr_number: int,
    head_sha: Optional[str],
    pr_title: Optional[str],
    result_json: str,
) -> Optional[int]:
    engine = create_db_engine()
    if not engine:
        return None
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        row = PrReviewReport(
            user_id=user_id,
            path_with_namespace=path_ns,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=pr_title,
            status="completed",
            result_json=result_json,
            error=None,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def _save_report_failed(
    user_id: int,
    path_ns: str,
    pr_number: int,
    head_sha: Optional[str],
    pr_title: Optional[str],
    err: str,
) -> None:
    engine = create_db_engine()
    if not engine:
        return
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        row = PrReviewReport(
            user_id=user_id,
            path_with_namespace=path_ns,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=pr_title,
            status="failed",
            result_json=None,
            error=err[:8000],
        )
        session.add(row)
        session.commit()


def upsert_user_from_gitee_token(
    token_json: dict[str, Any],
    gitee_user: dict[str, Any],
) -> AppUser:
    """创建或更新 Gitee 账号绑定，返回 AppUser。"""
    engine = create_db_engine()
    if not engine:
        raise RuntimeError("需要配置 DATABASE_URL 才能使用 Gitee 登录与报告")
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    access = token_json.get("access_token") or ""
    refresh = token_json.get("refresh_token")
    expires_in = token_json.get("expires_in")
    exp_at = None
    if expires_in is not None:
        try:
            exp_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in) - 60)
        except (TypeError, ValueError):
            exp_at = None

    gid = int(gitee_user["id"])
    login = str(gitee_user.get("login") or "")

    with Session(engine) as session:
        stmt = select(GiteeOAuthAccount).where(GiteeOAuthAccount.gitee_user_id == gid)
        acc = session.scalars(stmt).first()
        if acc:
            acc.access_token = access
            acc.refresh_token = refresh if refresh else acc.refresh_token
            acc.token_expires_at = exp_at
            acc.login = login
            user = session.get(AppUser, acc.user_id)
            if not user:
                raise RuntimeError("数据不一致：缺少 app_users 行")
            session.commit()
            session.refresh(user)
            return user

        user = AppUser()
        session.add(user)
        session.flush()
        acc = GiteeOAuthAccount(
            user_id=user.id,
            gitee_user_id=gid,
            login=login,
            access_token=access,
            refresh_token=refresh,
            token_expires_at=exp_at,
        )
        session.add(acc)
        session.commit()
        session.refresh(user)
        return user


def sync_hooks_for_user(user_id: int) -> dict[str, Any]:
    """为当前用户所有有权限的仓库注册合并请求 WebHook（去重）。"""
    engine = create_db_engine()
    if not engine:
        return {"ok": False, "error": "未配置 DATABASE_URL"}
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    secret = os.getenv("GITEE_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Debug hint for env wiring issues (Railway often mis-scoped vars / no restart).
        present = {k: bool(os.getenv(k, "").strip()) for k in ("GITEE_WEBHOOK_SECRET", "PUBLIC_BASE_URL", "DATABASE_URL")}
        logger.warning(
            "sync_hooks_for_user: missing GITEE_WEBHOOK_SECRET user_id=%s env_present=%s",
            user_id,
            present,
        )
        return {"ok": False, "error": "请配置 GITEE_WEBHOOK_SECRET（与 Gitee WebHook 密码一致）"}

    base = public_base_url()
    logger.info(
        "sync_hooks_for_user: begin user_id=%s base=%s secret_len=%s",
        user_id,
        base,
        len(secret),
    )
    with Session(engine) as session:
        stmt = select(GiteeOAuthAccount).where(GiteeOAuthAccount.user_id == user_id)
        acc = session.scalars(stmt).first()
        if not acc:
            return {"ok": False, "error": "未绑定 Gitee 账号"}
        user = session.get(AppUser, user_id)
        if not user:
            return {"ok": False, "error": "用户不存在"}
        token = acc.access_token
        hook_url = f"{base}/api/gitee/webhook/saas/{user.saas_webhook_token}"

        added = 0
        skipped = 0
        failed = 0
        page = 1
        while page <= 20:
            repos = list_user_repos(token, page=page, per_page=100)
            if not repos:
                break
            for repo in repos:
                pns = repo.get("path_with_namespace") or repo.get("full_name")
                if not pns:
                    continue
                pns = str(pns).strip()
                try:
                    owner, rname = split_path_with_namespace(pns)
                except ValueError:
                    failed += 1
                    continue
                hooks = list_repo_hooks(token, owner, rname)
                if any(h.get("url") == hook_url for h in hooks):
                    skipped += 1
                    wr = session.scalars(
                        select(GiteeWatchedRepo).where(
                            GiteeWatchedRepo.user_id == user_id,
                            GiteeWatchedRepo.path_with_namespace == pns,
                        )
                    ).first()
                    if not wr:
                        session.add(GiteeWatchedRepo(user_id=user_id, path_with_namespace=pns, hook_id=None))
                    continue
                created = create_repo_hook(token, owner, rname, url=hook_url, password=secret)
                if created:
                    hid = created.get("id")
                    try:
                        hid_int = int(hid) if hid is not None else None
                    except (TypeError, ValueError):
                        hid_int = None
                    wr = session.scalars(
                        select(GiteeWatchedRepo).where(
                            GiteeWatchedRepo.user_id == user_id,
                            GiteeWatchedRepo.path_with_namespace == pns,
                        )
                    ).first()
                    if wr:
                        wr.hook_id = hid_int
                    else:
                        session.add(
                            GiteeWatchedRepo(
                                user_id=user_id, path_with_namespace=pns, hook_id=hid_int
                            )
                        )
                    added += 1
                else:
                    failed += 1
            if len(repos) < 100:
                break
            page += 1
        session.commit()
        return {"ok": True, "data": {"added": added, "skipped": skipped, "failed": failed, "hook_url": hook_url}}
