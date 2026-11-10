#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-x-kol 离线端到端（fixtures 回放，不联网、不推送）。

由 `make gate` 自动发现并执行；退出码 0 = 通过。
覆盖：首次抓取建基线 → 主页出现新帖 → 增量抓取 → 生成日报。
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
DATE = "2026-11-08"
NEW_POST = (
    "\n*   [![Image 99: @saylor](https://pbs.twimg.com/profile_images/1/new_normal.jpg)]"
    "(https://x.com/saylor) ETF inflows are accelerating; BTC supply is tightening.\n"
)


def run(args):
    proc = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print("❌ 命令失败：%s\n%s\n%s" % (" ".join(args), proc.stdout, proc.stderr))
        raise SystemExit(1)
    return proc.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="crypto-x-kol-e2e-")
    try:
        fixtures = os.path.join(tmp, "fixtures")
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures"), fixtures)
        data = os.path.join(tmp, "data")
        only = "--only", "saylor,willywoo"
        fetch = [os.path.join(HERE, "fetch.py"), "--fixtures", fixtures,
                 "--data-dir", data, "--date", DATE] + list(only)

        out = run(fetch)
        if "新增 0 条" not in out:
            raise SystemExit("❌ 首次抓取应建基线（新增 0 条），实际：\n%s" % out)

        page = os.path.join(fixtures, "x.com", "saylor.md")
        with open(page, "a", encoding="utf-8") as f:
            f.write(NEW_POST)
        out = run(fetch)
        if "新增 1 条" not in out:
            raise SystemExit("❌ 第二次抓取应新增 1 条，实际：\n%s" % out)

        raw = os.path.join(data, "raw", DATE, "posts.jsonl")
        if not os.path.isfile(raw):
            raise SystemExit("❌ 缺少 raw：%s" % raw)

        digest = [os.path.join(HERE, "digest.py"), "--data-dir", data, "--date", DATE,
                  "--no-analyze"]
        preview = run(digest + ["--dry-run"])
        if "@saylor" not in preview:
            raise SystemExit("❌ 推送预览里没有 @saylor：\n%s" % preview)

        run(digest)  # 默认不推送，只写报告
        report = os.path.join(data, "reports", DATE + ".md")
        if not os.path.isfile(report):
            raise SystemExit("❌ 缺少日报：%s" % report)
        with open(report, "r", encoding="utf-8") as f:
            body = f.read()
        if "KOL 推文日报" not in body:
            raise SystemExit("❌ 日报内容异常")
        print("✅ crypto-x-kol e2e 通过（基线 → 增量 → 日报）")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
