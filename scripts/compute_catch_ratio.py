#!/usr/bin/env python3
"""
根据人工标注计算 Catch 比率报告。

用法:
  python scripts/compute_catch_ratio.py --labels review_output/xxx_eval_labels.csv

CSV 格式: 由 run_review_and_report.py 生成，需人工填写 human_caught 列:
  - 1: 该评论成功 catch 了真实问题（真阳性）
  - 0: 误报或无效建议（假阳性）
  - 空: 跳过，不参与计算
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def compute_catch_ratio(labels_path: Path) -> dict:
    """读取标注 CSV，计算 catch 比率。"""
    rows = []
    with open(labels_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        if "human_caught" not in headers:
            raise ValueError("CSV 需包含 human_caught 列")
        for r in reader:
            rows.append(r)

    labeled = []
    for i, r in enumerate(rows):
        val = (r.get("human_caught") or "").strip()
        if val == "":
            continue
        try:
            v = int(val)
            if v in (0, 1):
                labeled.append((i, v, r))
        except ValueError:
            continue

    total_labeled = len(labeled)
    caught = sum(1 for _, v, _ in labeled if v == 1)
    false_positives = total_labeled - caught

    precision = caught / total_labeled if total_labeled else 0.0
    catch_ratio = precision  # 成功 catch 的比率 = 真阳性 / 已标注总数

    by_severity = {}
    by_category = {}
    for _, v, r in labeled:
        sev = (r.get("severity") or "").strip() or "?"
        cat = (r.get("category") or "").strip() or "?"
        key = (sev, cat)
        if sev not in by_severity:
            by_severity[sev] = {"caught": 0, "total": 0}
        by_severity[sev]["total"] += 1
        if v == 1:
            by_severity[sev]["caught"] += 1
        if cat not in by_category:
            by_category[cat] = {"caught": 0, "total": 0}
        by_category[cat]["total"] += 1
        if v == 1:
            by_category[cat]["caught"] += 1

    return {
        "total_comments": len(rows),
        "total_labeled": total_labeled,
        "caught": caught,
        "false_positives": false_positives,
        "catch_ratio": catch_ratio,
        "precision": precision,
        "by_severity": {k: {"caught": v["caught"], "total": v["total"], "ratio": v["caught"] / v["total"] if v["total"] else 0} for k, v in by_severity.items()},
        "by_category": {k: {"caught": v["caught"], "total": v["total"], "ratio": v["caught"] / v["total"] if v["total"] else 0} for k, v in by_category.items()},
    }


def print_report(report: dict) -> None:
    """打印 Catch 比率报告。"""
    print("\n" + "=" * 60)
    print("Catch 比率报告")
    print("=" * 60)
    print(f"总评论数:     {report['total_comments']}")
    print(f"已标注数:     {report['total_labeled']}")
    print(f"成功 catch:   {report['caught']}")
    print(f"误报/无效:    {report['false_positives']}")
    print(f"-" * 40)
    print(f"Catch 比率:   {report['catch_ratio']:.1%}  (成功 catch / 已标注)")
    print(f"精确率:       {report['precision']:.1%}  (真阳性 / 已标注)")
    print("\n按严重程度:")
    for sev, d in report["by_severity"].items():
        print(f"  {sev}: {d['caught']}/{d['total']} = {d['ratio']:.1%}")
    print("\n按类别:")
    for cat, d in report["by_category"].items():
        print(f"  {cat}: {d['caught']}/{d['total']} = {d['ratio']:.1%}")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="根据人工标注计算 Catch 比率")
    parser.add_argument("--labels", "-l", required=True, help="标注 CSV 路径（run_review_and_report.py 生成的 _eval_labels.csv）")
    args = parser.parse_args()

    path = Path(args.labels)
    if not path.exists():
        print(f"错误: 文件不存在 {path}", file=sys.stderr)
        sys.exit(1)

    try:
        report = compute_catch_ratio(path)
        print_report(report)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
