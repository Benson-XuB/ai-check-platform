"""GitHub Checks API: create/update check runs for PR notification."""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from app.services.github_app import github_api_base, installation_auth_headers


def _api_base() -> str:
    return github_api_base().rstrip("/")


def create_check_run(
    *,
    installation_token: str,
    owner: str,
    repo: str,
    head_sha: str,
    name: str,
    details_url: str,
    summary: str = "Review in progress",
) -> int:
    """
    Create a check run in_progress. Returns check_run_id.
    Requires GitHub App permission: Checks: write.
    """
    url = f"{_api_base()}/repos/{owner}/{repo}/check-runs"
    hdr = {**installation_auth_headers(installation_token), "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": "in_progress",
        "details_url": details_url,
        "output": {"title": name, "summary": summary},
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.post(url, headers=hdr, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create check run failed: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    rid = data.get("id") if isinstance(data, dict) else None
    if rid is None:
        raise RuntimeError("create check run returned no id")
    return int(rid)


def complete_check_run(
    *,
    installation_token: str,
    owner: str,
    repo: str,
    check_run_id: int,
    details_url: str,
    conclusion: str,
    summary: str,
    title: Optional[str] = None,
) -> None:
    """
    Mark a check run completed.
    conclusion: success|neutral|failure|cancelled|timed_out|action_required|stale|skipped
    """
    url = f"{_api_base()}/repos/{owner}/{repo}/check-runs/{int(check_run_id)}"
    hdr = {**installation_auth_headers(installation_token), "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "status": "completed",
        "conclusion": conclusion,
        "details_url": details_url,
        "output": {
            "title": title or "AI Review",
            "summary": summary[:65000],
        },
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.patch(url, headers=hdr, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"complete check run failed: HTTP {r.status_code} {r.text[:200]}")

