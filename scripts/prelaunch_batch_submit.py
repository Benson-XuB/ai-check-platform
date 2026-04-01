import argparse
import sys
import time
from typing import List, Optional

import requests


def _read_repos_from_file(path: str) -> List[str]:
    repos: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            repos.append(s)
    return repos


def _poll_job(base_url: str, job_id: str, interval_sec: float = 2.0, timeout_sec: float = 900.0) -> None:
    t0 = time.time()
    last = None
    while True:
        r = requests.get(f"{base_url}/api/prelaunch/jobs/{job_id}", timeout=30)
        r.raise_for_status()
        data = r.json().get("data") or {}
        status = data.get("status")
        if status != last:
            print(f"[{job_id}] status={status}")
            last = status
        if status in ("complete", "failed"):
            return
        if time.time() - t0 > timeout_sec:
            print(f"[{job_id}] timeout after {timeout_sec}s", file=sys.stderr)
            return
        time.sleep(interval_sec)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Submit batch prelaunch jobs.")
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API base url, default http://127.0.0.1:8000",
    )
    p.add_argument("--file", help="Text file containing repo URLs, one per line.")
    p.add_argument("--git-token", default="", help="Optional git token for private repos.")
    p.add_argument("--ref", default="", help="Optional branch/tag.")
    p.add_argument("--llm-provider", default="dashscope", help="LLM provider, default dashscope.")
    p.add_argument("--llm-api-key", required=True, help="LLM API key (required).")
    p.add_argument("--watch", action="store_true", help="Poll job statuses until done.")
    p.add_argument("repo_urls", nargs="*", help="Repo URLs (HTTPS).")
    args = p.parse_args(argv)

    repo_urls: List[str] = []
    if args.file:
        repo_urls.extend(_read_repos_from_file(args.file))
    repo_urls.extend([x for x in args.repo_urls if (x or "").strip()])
    if not repo_urls:
        print("No repo URLs provided.", file=sys.stderr)
        return 2

    body = {
        "repo_urls": repo_urls,
        "git_token": args.git_token or None,
        "ref": args.ref or None,
        "llm_provider": args.llm_provider,
        "llm_api_key": args.llm_api_key,
    }
    r = requests.post(f"{args.base_url}/api/prelaunch/jobs/batch", json=body, timeout=60)
    r.raise_for_status()
    resp = r.json()
    job_ids = resp.get("job_ids") or []
    print("job_ids:")
    for jid in job_ids:
        print(jid)

    if args.watch:
        for jid in job_ids:
            _poll_job(args.base_url, jid)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

