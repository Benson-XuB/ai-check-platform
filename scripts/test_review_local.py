#!/usr/bin/env python3
"""
基于本地代码测试 Review 功能（无需真实 PR）。

用法:
  export DASHSCOPE_API_KEY="your_key"
  python scripts/test_review_local.py

或指定仓库路径:
  python scripts/test_review_local.py --repo test_repos/resumehub

脚本会:
  1. 对 test_repos/resumehub 中 backend/app/routers/candidates_risky.py 生成 diff
  2. 调用 4 维度审查
  3. 输出报告和 eval 模板
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_diff_for_new_file(file_path: Path, repo_root: Path) -> str:
    """为新文件生成 unified diff。"""
    rel = file_path.relative_to(repo_root)
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    result = [
        "diff --git a/" + str(rel) + " b/" + str(rel),
        "new file mode 100644",
        "index 0000000..1111111",
        "--- /dev/null",
        "+++ b/" + str(rel),
    ]
    for line in lines:
        result.append("+" + line)
    return "\n".join(result)


def gather_file_contexts(repo_root: Path, target_rel: str) -> dict:
    """收集相关文件的完整内容作为上下文。"""
    contexts = {}
    target_path = repo_root / target_rel
    if target_path.exists():
        contexts[target_rel] = target_path.read_text(encoding="utf-8")

    # 添加相关文件
    for rel in [
        "backend/app/routers/candidates.py",
        "backend/app/models.py",
        "backend/app/database.py",
    ]:
        p = repo_root / rel
        if p.exists() and rel not in contexts:
            contexts[rel] = p.read_text(encoding="utf-8")
    return contexts


def run_review(diff: str, file_contexts: dict, llm_key: str, output_dir: Path) -> dict:
    """调用 4 维度审查并保存报告。"""
    from app.services import review as review_svc

    comments = review_svc.review_multidim(
        diff,
        llm_key,
        pr_title="[测试] 新增 candidates_risky 模块（含故意 bug）",
        pr_body="本 PR 用于测试 Review 的 catch 能力。candidates_risky.py 内含：SQL 注入、硬编码密钥、缺少边界检查、错误泄露、幻觉 API 等。",
        file_contexts=file_contexts,
        repo_key="dlust-university/resumehub",
    )

    # 构建报告
    by_severity = {}
    by_category = {}
    for c in comments:
        sev = (c.get("severity") or "Minor").strip() or "Minor"
        cat = (c.get("category") or "").strip() or "(无)"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_category[cat] = by_category.get(cat, 0) + 1

    report = {
        "meta": {
            "test_type": "local_resumehub",
            "timestamp": datetime.now().isoformat(),
        },
        "summary": {
            "total_comments": len(comments),
            "by_severity": dict(sorted(by_severity.items(), key=lambda x: -x[1])),
            "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        },
        "comments": comments,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"local_test_{ts}.json"
    csv_path = output_dir / f"local_test_{ts}_eval_labels.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "file", "line", "severity", "category", "suggestion", "human_caught"])
        for i, c in enumerate(comments):
            w.writerow([
                i, c.get("file", ""), c.get("line", ""),
                c.get("severity", ""), c.get("category", ""),
                (c.get("suggestion") or "")[:200], "",
            ])

    report["_json_path"] = str(json_path)
    report["_csv_path"] = str(csv_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="test_repos/resumehub", help="仓库路径")
    parser.add_argument("--output-dir", "-o", default="./review_output", help="输出目录")
    parser.add_argument("--llm-key", default=os.getenv("DASHSCOPE_API_KEY"), help="通义千问 API Key")
    args = parser.parse_args()

    llm_key = args.llm_key or ""
    if not llm_key:
        print("错误: 需设置 DASHSCOPE_API_KEY 或 --llm-key", file=sys.stderr)
        sys.exit(1)

    repo_root = ROOT / args.repo
    risky_file = repo_root / "backend/app/routers/candidates_risky.py"
    if not risky_file.exists():
        print(f"错误: 测试文件不存在 {risky_file}", file=sys.stderr)
        print("请确保已 clone resumehub 到 test_repos/resumehub", file=sys.stderr)
        sys.exit(1)

    print("正在生成 diff...")
    diff = make_diff_for_new_file(risky_file, repo_root)
    file_contexts = gather_file_contexts(repo_root, "backend/app/routers/candidates_risky.py")

    print("正在运行 4 维度审查（约 2-4 分钟）...")
    output_dir = Path(args.output_dir)
    report = run_review(diff, file_contexts, llm_key, output_dir)

    s = report["summary"]
    print("\n" + "=" * 60)
    print("本地 Review 测试报告")
    print("=" * 60)
    print(f"总评论数: {s['total_comments']}")
    print("\n按严重程度:", s["by_severity"])
    print("按类别:", s["by_category"])
    print("\nJSON:", report["_json_path"])
    print("标注模板:", report["_csv_path"])
    print("\n预期能 catch 的问题: SQL 注入、硬编码密钥、边界检查、错误泄露、幻觉 API")
    print("人工标注 human_caught 后运行: python scripts/compute_catch_ratio.py -l", report["_csv_path"])
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
