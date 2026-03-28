"""多平台 PR API（推荐）；与 /api/gitee/* 同一套处理与限流。"""

from fastapi import APIRouter, Request

from app.routers.gitee import (
    FetchPRRequest,
    PostCommentRequest,
    fetch_pr as gitee_fetch_pr_handler,
    post_comment as gitee_post_comment_handler,
)

router = APIRouter(prefix="/api/vcs", tags=["vcs"])


@router.post("/fetch-pr")
def vcs_fetch_pr(request: Request, req: FetchPRRequest):
    return gitee_fetch_pr_handler(request, req)


@router.post("/post-comment")
def vcs_post_comment(request: Request, req: PostCommentRequest):
    return gitee_post_comment_handler(request, req)
