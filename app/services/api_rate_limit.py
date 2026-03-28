"""公网暴露时常用 API 的限流（与 Prelaunch 建任务独立计数）。"""

import os

from fastapi import Request

from app.services.sliding_rate_limit import enforce


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def enforce_vcs_fetch_pr(request: Request) -> None:
    cap = _i("VCS_FETCH_RATE_LIMIT_MAX", 30)
    win = _i("VCS_FETCH_RATE_LIMIT_WINDOW_SEC", 3600)
    enforce(
        request,
        namespace="vcs:fetch_pr",
        max_hits=cap,
        window_sec=win,
        detail=(
            f"PR 拉取过于频繁：每 {win // 60} 分钟最多 {cap} 次（每 IP）。"
            "请稍后再试；自建部署可调 VCS_FETCH_RATE_LIMIT_MAX / WINDOW。"
        ),
    )


def enforce_review_llm(request: Request) -> None:
    cap = _i("REVIEW_RATE_LIMIT_MAX", 20)
    win = _i("REVIEW_RATE_LIMIT_WINDOW_SEC", 3600)
    enforce(
        request,
        namespace="review:llm",
        max_hits=cap,
        window_sec=win,
        detail=(
            f"AI 审查调用过多：每 {win // 60} 分钟最多 {cap} 次（每 IP）。"
            "请稍后再试；可调 REVIEW_RATE_LIMIT_MAX。"
        ),
    )


def enforce_vcs_post_comment(request: Request) -> None:
    cap = _i("VCS_COMMENT_RATE_LIMIT_MAX", 80)
    win = _i("VCS_COMMENT_RATE_LIMIT_WINDOW_SEC", 3600)
    enforce(
        request,
        namespace="vcs:post_comment",
        max_hits=cap,
        window_sec=win,
        detail=(
            f"发评论过于频繁：每 {win // 60} 分钟最多 {cap} 次（每 IP）。"
            "可调 VCS_COMMENT_RATE_LIMIT_MAX。"
        ),
    )


def enforce_rag_index_from_pr(request: Request) -> None:
    cap = _i("RAG_PR_INDEX_RATE_LIMIT_MAX", 15)
    win = _i("RAG_PR_INDEX_RATE_LIMIT_WINDOW_SEC", 3600)
    enforce(
        request,
        namespace="rag:index_from_pr",
        max_hits=cap,
        window_sec=win,
        detail=(
            f"代码索引请求过多：每 {win // 60} 分钟最多 {cap} 次（每 IP）。"
            "可调 RAG_PR_INDEX_RATE_LIMIT_MAX。"
        ),
    )
