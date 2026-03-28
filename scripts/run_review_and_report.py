#!/usr/bin/env python3
"""
运行 PR 审查并生成 Catch 比率报告。

用法:
  1. 设置环境变量（推荐）:
     export GITEE_TOKEN="your_gitee_token"
     export DASHSCOPE_API_KEY="your_dashscope_key"

  2. 运行:
     python scripts/run_review_and_report.py https://gitee.com/owner/repo/pulls/123

  3. 人工标注后计算 catch 比率:
     python scripts/compute_catch_ratio.py --labels output/eval_labels.csv

可选参数:
  --enrich           包含测试与 import 相关文件
  --pyright          启用 Pyright 类型检查
  --dimension        使用 4 维度串行审查（推荐）
  --multipass        使用三阶段审查
  --output-dir DIR   输出目录，默认 ./review_output
  --api-url URL      通过 HTTP 调用（需先启动 uvicorn），不传则直接调用服务
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_via_api(
    api_url: str,
    pr_url: str,
    gitee_token: str,
    llm_key: str,
    *,
    enrich: bool = False,
    pyright: bool = False,
    dimension: bool = True,
    multipass: bool = False,
) -> tuple[dict, list]:
    """通过 HTTP API 拉取 PR 并审查。"""
    import httpx

    base = api_url.rstrip("/")
    with httpx.Client(timeout=180) as client:
        r = client.post(
            f"{base}/api/gitee/fetch-pr",
            json={
                "pr_url": pr_url,
                "gitee_token": gitee_token,
                "enrich_context": enrich,
                "use_pyright": pyright,
                "use_treesitter": pyright,
                "use_symbol_graph": False,
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"fetch-pr 失败: {r.status_code} {r.text[:500]}")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"fetch-pr 错误: {data.get('error', 'unknown')}")

        pr_data = data["data"]
        repo_key = f"{pr_data['owner']}/{pr_data['repo']}" if pr_data.get("owner") and pr_data.get("repo") else None

        r2 = client.post(
            f"{base}/api/review",
            json={
                "diff": pr_data["diff"],
                "pr_title": pr_data.get("title", ""),
                "pr_body": pr_data.get("body", ""),
                "file_contexts": pr_data.get("file_contexts") or {},
                "llm_provider": "dashscope",
                "llm_api_key": llm_key,
                "use_dimension_review": dimension,
                "use_multipass": multipass and not dimension,
                "repo_key": repo_key,
            },
        )
        if r2.status_code != 200:
            raise RuntimeError(f"review 失败: {r2.status_code} {r2.text[:500]}")
        rev = r2.json()
        if not rev.get("ok"):
            raise RuntimeError(f"review 错误: {rev.get('error', 'unknown')}")

        return pr_data, rev["data"]["comments"]


def run_direct(
    pr_url: str,
    gitee_token: str,
    llm_key: str,
    *,
    enrich: bool = False,
    pyright: bool = False,
    dimension: bool = True,
    multipass: bool = False,
) -> tuple[dict, list]:
    """直接调用服务，无需启动 HTTP 服务。"""
    from app.services import gitee as gitee_svc
    from app.services import context_enrichment as enrichment_svc
    from app.services import pyright_analyzer as pyright_svc
    from app.services import treesitter_analyzer as treesitter_svc
    from app.services import review as review_svc

    result = gitee_svc.fetch_pr(pr_url, gitee_token)
    if not result["ok"]:
        raise RuntimeError(f"fetch-pr 错误: {result.get('error', 'unknown')}")

    pr_data = result["data"]
    if enrich and pr_data.get("head_sha"):
        owner = pr_data["owner"]
        repo = pr_data["repo"]
        head_sha = pr_data["head_sha"]
        changed = pr_data.get("changed_files") or []

        def fetch_file(path: str):
            return gitee_svc.fetch_file_content(owner, repo, path, head_sha, gitee_token)

        repo_tree = gitee_svc.get_repo_tree_paths(owner, repo, head_sha, gitee_token) or None
        pr_data["file_contexts"] = enrichment_svc.enrich_file_contexts(
            pr_data["file_contexts"],
            changed,
            fetch_file,
            add_tests=True,
            add_imports=True,
            repo_tree_paths=repo_tree,
        )
        if pyright:
            summary = treesitter_svc.summarize_changes(
                pr_data.get("diff") or "", pr_data["file_contexts"], changed
            )
            pr_data["change_kind"] = summary.change_kind
            if summary.change_kind != "comment_only":
                pr_res = pyright_svc.run_pyright_in_sandbox(
                    owner=owner,
                    repo=repo,
                    sha=head_sha,
                    file_contexts=pr_data["file_contexts"],
                    fetch_file=fetch_file,
                )
                diag = pr_res.diagnostics or []
                if diag:
                    pr_data["file_contexts"]["[pyright diagnostics]"] = json.dumps(
                        diag[:30], ensure_ascii=False, indent=2
                    )[:20000]

    repo_key = f"{pr_data['owner']}/{pr_data['repo']}" if pr_data.get("owner") and pr_data.get("repo") else None

    if dimension:
        comments = review_svc.review_multidim(
            pr_data["diff"],
            llm_key,
            pr_title=pr_data.get("title", ""),
            pr_body=pr_data.get("body", ""),
            file_contexts=pr_data.get("file_contexts") or {},
            repo_key=repo_key,
        )
    elif multipass:
        comments = review_svc.review_multipass(
            pr_data["diff"],
            llm_key,
            pr_title=pr_data.get("title", ""),
            pr_body=pr_data.get("body", ""),
            file_contexts=pr_data.get("file_contexts") or {},
        )
    else:
        comments = review_svc.call_dashscope(
            pr_data["diff"],
            llm_key,
            pr_title=pr_data.get("title", ""),
            pr_body=pr_data.get("body", ""),
            file_contexts=pr_data.get("file_contexts") or {},
        )

    return pr_data, comments


def build_report(comments: list, pr_data: dict) -> dict:
    """构建统计报告。"""
    by_severity = {}
    by_category = {}
    by_file = {}

    for c in comments:
        sev = (c.get("severity") or "Minor").strip() or "Minor"
        cat = (c.get("category") or "").strip() or "(无)"
        fn = (c.get("file") or "").strip() or "(整体)"

        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_category[cat] = by_category.get(cat, 0) + 1
        by_file[fn] = by_file.get(fn, 0) + 1

    return {
        "meta": {
            "pr_url": pr_data.get("pr_url", ""),
            "pr_title": pr_data.get("title", ""),
            "owner": pr_data.get("owner", ""),
            "repo": pr_data.get("repo", ""),
            "number": pr_data.get("number", ""),
            "timestamp": datetime.now().isoformat(),
        },
        "summary": {
            "total_comments": len(comments),
            "by_severity": dict(sorted(by_severity.items(), key=lambda x: -x[1])),
            "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
            "by_file": dict(sorted(by_file.items(), key=lambda x: -x[1])),
        },
        "comments": comments,
    }


def save_output(report: dict, output_dir: Path, pr_slug: str) -> tuple[Path, Path]:
    """保存 JSON 报告和 eval CSV 模板。返回 (json_path, csv_path)。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_dir / f"{pr_slug}_{ts}"

    json_path = Path(f"{base}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    csv_path = Path(f"{base}_eval_labels.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "file", "line", "severity", "category", "suggestion", "human_caught"])
        for i, c in enumerate(report["comments"]):
            w.writerow([
                i,
                c.get("file", ""),
                c.get("line", ""),
                c.get("severity", ""),
                c.get("category", ""),
                (c.get("suggestion") or "")[:200],
                "",  # 人工填写: 1=成功 catch 真实问题, 0=误报/无效, 空=未标注
            ])

    return json_path, csv_path


def print_report(report: dict, json_path: Path, csv_path: Path) -> None:
    """打印报告摘要。"""
    s = report["summary"]
    print("\n" + "=" * 60)
    print("审查报告")
    print("=" * 60)
    print(f"PR: {report['meta'].get('pr_title', '')}")
    print(f"总评论数: {s['total_comments']}")
    print("\n按严重程度:")
    for k, v in s["by_severity"].items():
        print(f"  {k}: {v}")
    print("\n按类别:")
    for k, v in s["by_category"].items():
        print(f"  {k}: {v}")
    print("\n按文件:")
    for k, v in list(s["by_file"].items())[:10]:
        print(f"  {k}: {v}")
    if len(s["by_file"]) > 10:
        print(f"  ... 共 {len(s['by_file'])} 个文件")
    print("\n" + "-" * 60)
    print(f"JSON 报告: {json_path}")
    print(f"标注模板:  {csv_path}")
    print("人工标注 human_caught 列 (1=有效 catch/0=误报) 后运行:")
    print(f"  python scripts/compute_catch_ratio.py --labels {csv_path}")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 PR 审查并生成 Catch 比率报告")
    parser.add_argument("pr_url", help="Gitee PR 链接")
    parser.add_argument("--gitee-token", default=os.getenv("GITEE_TOKEN"), help="Gitee Token (或环境变量 GITEE_TOKEN)")
    parser.add_argument("--llm-key", default=os.getenv("DASHSCOPE_API_KEY"), help="通义千问 API Key (或环境变量 DASHSCOPE_API_KEY)")
    parser.add_argument("--api-url", default="", help="通过 HTTP 调用时的 base URL，如 http://127.0.0.1:8000")
    parser.add_argument("--enrich", action="store_true", help="包含测试与 import 相关文件")
    parser.add_argument("--pyright", action="store_true", help="启用 Pyright")
    parser.add_argument("--dimension", action="store_true", default=True, help="4 维度串行审查 (默认)")
    parser.add_argument("--no-dimension", action="store_true", help="不使用 4 维度")
    parser.add_argument("--multipass", action="store_true", help="三阶段审查 (与 dimension 二选一)")
    parser.add_argument("--output-dir", "-o", default="./review_output", help="输出目录")
    args = parser.parse_args()

    gitee_token = args.gitee_token or ""
    llm_key = args.llm_key or ""
    if not gitee_token:
        print("错误: 需要 Gitee Token。请设置环境变量 GITEE_TOKEN 或使用 --gitee-token", file=sys.stderr)
        sys.exit(1)
    if not llm_key:
        print("错误: 需要通义千问 API Key。请设置环境变量 DASHSCOPE_API_KEY 或使用 --llm-key", file=sys.stderr)
        sys.exit(1)

    use_dimension = args.dimension and not args.no_dimension
    if use_dimension and args.multipass:
        args.multipass = False
        print("注意: dimension 与 multipass 同时开启时，优先使用 dimension")

    # 生成 pr_slug 用于文件名
    import re
    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", args.pr_url.strip())
    pr_slug = f"{m.group(1)}_{m.group(2)}_pr{m.group(3)}" if m else "pr"

    print("正在拉取 PR...")
    try:
        if args.api_url:
            pr_data, comments = run_via_api(
                args.api_url,
                args.pr_url,
                gitee_token,
                llm_key,
                enrich=args.enrich,
                pyright=args.pyright,
                dimension=use_dimension,
                multipass=args.multipass,
            )
        else:
            pr_data, comments = run_direct(
                args.pr_url,
                gitee_token,
                llm_key,
                enrich=args.enrich,
                pyright=args.pyright,
                dimension=use_dimension,
                multipass=args.multipass,
            )
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    pr_data["pr_url"] = args.pr_url
    report = build_report(comments, pr_data)
    output_dir = Path(args.output_dir)
    json_path, csv_path = save_output(report, output_dir, pr_slug)
    print_report(report, json_path, csv_path)


if __name__ == "__main__":
    main()
