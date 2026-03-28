"""多平台 PR 请求体与 URL 解析。"""

from app.routers.gitee import FetchPRRequest, PostCommentRequest
from app.services.github_pr import parse_pr_url
from app.services.vcs_dispatch import fetch_pr


def test_fetch_pr_request_accepts_legacy_gitee_token():
    r = FetchPRRequest.model_validate(
        {"pr_url": "https://gitee.com/a/b/pulls/1", "gitee_token": "secret"}
    )
    assert r.vcs_token == "secret"
    assert r.platform == "gitee"


def test_fetch_pr_request_accepts_vcs_token_and_platform():
    r = FetchPRRequest.model_validate(
        {"platform": "github", "pr_url": "https://github.com/o/r/pull/2", "vcs_token": "ghp_x"}
    )
    assert r.platform == "github"
    assert r.vcs_token == "ghp_x"


def test_post_comment_request_alias():
    r = PostCommentRequest.model_validate(
        {
            "owner": "o",
            "repo": "r",
            "number": "3",
            "comment": "hi",
            "gitee_token": "t",
        }
    )
    assert r.vcs_token == "t"


def test_parse_github_pr_url():
    assert parse_pr_url("https://github.com/foo/bar/pull/9") == {
        "owner": "foo",
        "repo": "bar",
        "number": "9",
    }
    assert parse_pr_url("https://github.com/foo/bar/pull/9/files") == {
        "owner": "foo",
        "repo": "bar",
        "number": "9",
    }


def test_fetch_pr_unknown_platform():
    out = fetch_pr("gitlab", "https://gitee.com/a/b/pulls/1", "t")
    assert out["ok"] is False
