"""多平台 PR 拉取与评论：统一入口，按 platform 分发。"""

from typing import List, Optional

from app.services import gitee as gitee_svc
from app.services import github_pr as github_svc

SUPPORTED_FETCH = frozenset({"gitee", "github"})
SUPPORTED_COMMENT = frozenset({"gitee", "github"})


def normalize_platform(raw: str) -> str:
    return (raw or "gitee").lower().strip()


def fetch_pr(platform: str, pr_url: str, token: str) -> dict:
    p = normalize_platform(platform)
    if p == "gitee":
        return gitee_svc.fetch_pr(pr_url, token)
    if p == "github":
        return github_svc.fetch_pr(pr_url, token)
    return {"ok": False, "error": f"不支持的平台: {p}。当前支持: gitee、github。"}


def fetch_file_content(platform: str, owner: str, repo: str, path: str, ref: str, token: str) -> Optional[str]:
    p = normalize_platform(platform)
    if p == "gitee":
        return gitee_svc.fetch_file_content(owner, repo, path, ref, token)
    if p == "github":
        return github_svc.fetch_file_content(owner, repo, path, ref, token)
    return None


def get_repo_tree_paths(platform: str, owner: str, repo: str, ref: str, token: str) -> List[str]:
    p = normalize_platform(platform)
    if p == "gitee":
        return gitee_svc.get_repo_tree_paths(owner, repo, ref, token)
    if p == "github":
        return github_svc.get_repo_tree_paths(owner, repo, ref, token)
    return []


def post_comment(
    platform: str,
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
    p = normalize_platform(platform)
    if p == "gitee":
        return gitee_svc.post_comment(
            owner, repo, number, comment, token, path=path, line=line, commit_id=commit_id, diff=diff
        )
    if p == "github":
        return github_svc.post_comment(
            owner, repo, number, comment, token, path=path, line=line, commit_id=commit_id, diff=diff
        )
    return {"ok": False, "error": f"不支持的平台: {p}"}
