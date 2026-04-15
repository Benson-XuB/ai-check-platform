import pytest

from app.services.gitee_saas import parse_gitee_pull_url


def test_parse_gitee_pull_url_ok():
    path, n = parse_gitee_pull_url("https://gitee.com/org/sub/repo/pulls/42")
    assert path == "org/sub/repo"
    assert n == 42


def test_parse_gitee_pull_url_strips_slash():
    path, n = parse_gitee_pull_url("https://gitee.com/a/b/pulls/1/")
    assert path == "a/b"
    assert n == 1


def test_parse_gitee_pull_url_rejects_non_gitee():
    with pytest.raises(ValueError):
        parse_gitee_pull_url("https://github.com/a/b/pull/1")


def test_parse_gitee_pull_url_rejects_without_pulls():
    with pytest.raises(ValueError):
        parse_gitee_pull_url("https://gitee.com/a/b")
