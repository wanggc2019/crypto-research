#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e.py —— crypto-jobs 离线端到端回放（make gate / CI 使用）。

用 tests/fixtures/sources 的原始响应回放两个日期 + 一次周报，断言：
- 快照、索引、日报、周报、exports 都生成；
- 输出是合法 JSON，岗位数 > 0；
- 全程不联网（fetch_all 走 fixtures 分支）。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
MAIN = os.path.join(ROOT, "scripts", "main.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "sources")


def _run(args, data_dir, date):
    cmd = [PY, MAIN, "--fixtures", FIXTURES, "--data-dir", data_dir, "--date", date] + args
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    return proc


def _json_of(proc):
    text = (proc.stdout or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def _require(ok, message):
    if not ok:
        print("❌ e2e 失败：%s" % message)
        sys.exit(1)


def main():
    if not os.path.isdir(FIXTURES):
        print("❌ 缺少离线 fixtures：%s（应在 tests/fixtures/sources）" % FIXTURES)
        return 1
    tmp = tempfile.mkdtemp(prefix="crypto-jobs-e2e-")
    try:
        first = _run(["--json"], tmp, "2026-11-08")
        _require(first.returncode in (0, 1), "首轮日更失败 rc=%s\n%s" % (
            first.returncode, (first.stderr or "")[-2000:]))
        summary = _json_of(first)
        _require(summary is not None, "首轮没有输出合法 JSON")
        _require((summary.get("overview") or {}).get("jobs", 0) > 0, "首轮回放岗位数为 0")
        _require(os.path.isfile(os.path.join(tmp, "state", "snapshots", "2026-11-08.json")),
                 "缺少快照 state/snapshots/2026-11-08.json")
        _require(os.path.isfile(os.path.join(tmp, "reports", "2026-11-08.md")), "缺少日报")

        second = _run(["--json"], tmp, "2026-11-09")
        _require(second.returncode in (0, 1), "第二轮日更失败")
        _require(os.path.isfile(os.path.join(tmp, "state", "snapshots", "2026-11-09.json")),
                 "缺少第二轮快照")
        _require(os.path.isfile(os.path.join(tmp, "state", "jobs-index.json")), "缺少 jobs-index.json")
        _require(os.path.isfile(os.path.join(tmp, "exports", "jobs-latest.json")), "缺少 jobs-latest 导出")
        _require(os.path.isfile(os.path.join(tmp, "exports", "company-stats.json")), "缺少 company-stats 导出")
        with open(os.path.join(tmp, "exports", "company-stats.json"), "r", encoding="utf-8") as f:
            company_stats = json.load(f)
        _require(company_stats.get("schema") == "company-stats/v1", "company-stats schema 不对")
        _require(bool(company_stats.get("companies")), "company-stats 没有公司数据")

        weekly = _run(["--weekly", "--json"], tmp, "2026-11-09")
        _require(weekly.returncode == 0, "周报失败 rc=%s\n%s" % (
            weekly.returncode, (weekly.stderr or "")[-2000:]))
        weekly_path = os.path.join(tmp, "reports", "2026-11-09-weekly.md")
        _require(os.path.isfile(weekly_path), "缺少周报")
        with open(weekly_path, "r", encoding="utf-8") as f:
            md = f.read()
        _require("技能需求 Top" in md and "方法与局限" in md, "周报缺少关键小节")
        print("✅ e2e 通过：快照/索引/日报/周报/exports 均生成（离线 fixtures）")
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
