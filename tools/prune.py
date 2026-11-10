#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prune.py —— 运行数据保留清理（默认 dry-run，只打印将删什么）。

保留策略（见 docs/runbook.md §6、docs/conventions.md §4）：
    data/raw/<YYYY-MM-DD>/       保留 90 天
    data/logs/*                  保留 30 天；单文件 > 2MB 只留最后 2000 行
    data/exports/*               保留 7 天
    data/reports/*.md            保留 365 天（人读产物，压缩不了就删旧的）
    data/state/alerts-<date>.json 保留 180 天（推送只读当天，旧的可删）
    data/state/.prune-*          保留 7 天（每日维护标记）
    data/state/ 其余             不删（fetch.py 已对 seen 做规模上限）

兜底：**总数据量硬上限** --max-data-mb（默认 500MB）。超过时按「最老的 raw 日期目录 →
最老的 exports → 最老的 reports → 最老的 alerts → 裁剪日志」依次删，直到回到预算内；
今天的 raw / alerts / 日志受保护，不会为了预算删掉正在用的数据（若仅今天的数据就超预算，
会打印警告，需要人工看是不是源异常）。

用法：
    python3 tools/prune.py --module crypto-news            # dry-run
    python3 tools/prune.py --module crypto-news --apply
    python3 tools/prune.py --all --apply
    python3 tools/prune.py --module crypto-news --max-data-mb 300 --apply
"""
import argparse
import datetime
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.date.today()


def _cutoff(days):
    return time.time() - days * 86400


def _module_dir(module):
    return REPO / module


def _size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _parse_day(name):
    try:
        return datetime.datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None


def _raw_days(module):
    """[(date, path)]，按日期升序。"""
    folder = _module_dir(module) / "data" / "raw"
    if not folder.is_dir():
        return []
    out = []
    for day in folder.iterdir():
        if day.is_dir():
            d = _parse_day(day.name)
            if d:
                out.append((d, day))
    return sorted(out)


def _date_files(folder, pattern, extract):
    """按文件名里的日期收集 [(date, path)]，按日期升序。"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for f in folder.glob(pattern):
        if not f.is_file():
            continue
        d = extract(f.name)
        if d:
            out.append((d, f))
    return sorted(out)


def candidates(module, raw_days=90, log_days=30, export_days=7, report_days=365, state_days=180):
    """返回 [(path, kind, is_dir)] 按保留期应清理的清单。"""
    data = _module_dir(module) / "data"
    out = []

    raw_cutoff = _cutoff(raw_days)
    for d, path in _raw_days(module):
        if datetime.datetime.combine(d, datetime.time()).timestamp() < raw_cutoff:
            out.append((path, "raw", True))

    for sub, days in (("logs", log_days), ("exports", export_days)):
        folder = data / sub
        if not folder.is_dir():
            continue
        cutoff = _cutoff(days)
        for f in folder.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                out.append((f, sub, False))

    reports = data / "reports"
    report_cutoff = _cutoff(report_days)
    for d, f in _date_files(reports, "*.md", lambda n: _parse_day(n[:-3])):
        if datetime.datetime.combine(d, datetime.time()).timestamp() < report_cutoff:
            out.append((f, "reports", False))

    state = data / "state"
    state_cutoff = _cutoff(state_days)
    for d, f in _date_files(state, "alerts-*.json", lambda n: _parse_day(n[len("alerts-"):-len(".json")])):
        if datetime.datetime.combine(d, datetime.time()).timestamp() < state_cutoff:
            out.append((f, "state", False))
    return out


def trim_logs(module, apply=False, max_log_kb=2048, keep_lines=2000):
    """单文件体积上限：超过阈值时只保留最后 keep_lines 行（防止日志无界增长）。"""
    out = []
    folder = _module_dir(module) / "data" / "logs"
    if not folder.is_dir():
        return out
    limit = max_log_kb * 1024
    for f in sorted(folder.glob("*.log")):
        if not f.is_file() or f.stat().st_size <= limit:
            continue
        before = f.stat().st_size
        out.append((f, before))
        if apply:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            kept = "\n".join(lines[-keep_lines:]) + "\n"
            tmp = f.with_suffix(f.suffix + ".tmp")
            tmp.write_text(kept, encoding="utf-8")
            tmp.replace(f)
    return out


def clean_markers(module, apply=False, days=7):
    """清理每日维护标记（data/state/.prune-<date>）。"""
    folder = _module_dir(module) / "data" / "state"
    if not folder.is_dir():
        return 0
    cutoff = _cutoff(days)
    n = 0
    for f in folder.glob(".prune-*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            n += 1
            if apply:
                f.unlink()
    return n


def _remove(path, is_dir, apply, removed):
    """执行/记录一次删除；返回删除的字节数。"""
    size = _size(path)
    if apply:
        if is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
    removed.append((path, "budget", is_dir))
    return size


def enforce_budget(module, max_mb=500, apply=False, keep_lines=200):
    """总数据量硬上限：超过预算时从最老的产物删起，直到回到预算内。"""
    limit = max_mb * 1024 * 1024
    data = _module_dir(module) / "data"
    total = _size(data)
    if total <= limit:
        return []

    removed = []
    budget = [total]

    def take(path, is_dir):
        if budget[0] <= limit:
            return
        budget[0] -= _remove(path, is_dir, apply, removed)

    # 1) 最老的 raw 日期目录（保护今天）
    for d, path in _raw_days(module):
        if d >= TODAY:
            continue
        take(path, True)

    # 2) 最老的 exports
    for f in sorted((data / "exports").glob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0) if (
        data / "exports"
    ).is_dir() else []:
        if f.is_file():
            take(f, False)

    # 3) 最老的 reports
    for _, f in _date_files(data / "reports", "*.md", lambda n: _parse_day(n[:-3])):
        take(f, False)

    # 4) 最老的 alerts（保护今天）
    for d, f in _date_files(data / "state", "alerts-*.json", lambda n: _parse_day(n[len("alerts-"):-len(".json")])):
        if d < TODAY:
            take(f, False)

    # 5) 最后手段：裁剪日志到最近 keep_lines 行
    if budget[0] > limit:
        for f in sorted((data / "logs").glob("*.log")):
            if budget[0] <= limit:
                break
            if not f.is_file():
                continue
            before = f.stat().st_size
            if apply:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                kept = "\n".join(lines[-keep_lines:]) + "\n"
                tmp = f.with_suffix(f.suffix + ".tmp")
                tmp.write_text(kept, encoding="utf-8")
                tmp.replace(f)
            budget[0] -= max(0, before - min(before, keep_lines * 100))
            removed.append((f, "budget-trim", False))

    if budget[0] > limit:
        print(
            "  ⚠️ 仅今天的数据就有 %.1f MB，超过预算 %s MB；未删除进行中的数据，请检查是否有源异常"
            % (budget[0] / 1024.0 / 1024.0, max_mb)
        )
    return removed


def prune_module(
    module, apply=False, raw_days=90, log_days=30, export_days=7, max_log_kb=2048,
    report_days=365, state_days=180, max_data_mb=500,
):
    items = candidates(module, raw_days, log_days, export_days, report_days, state_days)
    for path, kind, is_dir in items:
        verb = "删除" if apply else "将删除"
        print("  %s [%s] %s" % (verb, kind, path.relative_to(REPO)))
        if apply:
            if is_dir:
                shutil.rmtree(path)
            else:
                path.unlink()

    for path, before in trim_logs(module, apply, max_log_kb):
        verb = "已裁剪" if apply else "将裁剪"
        print("  %s [log] %s（%.1f MB → 最后 2000 行）" % (verb, path.relative_to(REPO), before / 1024.0 / 1024.0))
        items.append((path, "log", False))

    markers = clean_markers(module, apply)
    if markers:
        print("  %s [state] 清理 %d 个过期维护标记" % ("已清理" if apply else "将清理", markers))

    budget_removed = enforce_budget(module, max_data_mb, apply)
    for path, kind, _is_dir in budget_removed:
        verb = "删除" if apply else "将删除"
        print("  %s [%s] %s（超总预算）" % (verb, kind, path.relative_to(REPO)))
        items.append((path, kind, False))
    return len(items)


def all_modules():
    return sorted(p.name for p in REPO.iterdir() if p.is_dir() and p.name.startswith("crypto-"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="按保留策略清理运行数据（默认 dry-run）")
    parser.add_argument("--module", action="append", help="模块名（可重复）；默认全部")
    parser.add_argument("--all", action="store_true", help="所有 crypto-* 模块")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只打印）")
    parser.add_argument("--raw-days", type=int, default=90)
    parser.add_argument("--log-days", type=int, default=30)
    parser.add_argument("--export-days", type=int, default=7)
    parser.add_argument("--report-days", type=int, default=365, help="reports/*.md 保留天数")
    parser.add_argument("--state-days", type=int, default=180, help="state/alerts-*.json 保留天数")
    parser.add_argument("--max-log-kb", type=int, default=2048, help="单日志文件体积上限（KB），超出只留最后 2000 行")
    parser.add_argument("--max-data-mb", type=int, default=500, help="模块 data/ 总大小硬上限（MB）")
    args = parser.parse_args(argv)

    modules = args.module or (all_modules() if args.all else [])
    if not modules:
        print("请用 --module <name> 或 --all 指定模块")
        return 2
    total = 0
    for module in modules:
        if not (REPO / module).is_dir():
            print("跳过（不存在）：%s" % module)
            continue
        print("== %s ==" % module)
        total += prune_module(
            module,
            args.apply,
            args.raw_days,
            args.log_days,
            args.export_days,
            args.max_log_kb,
            args.report_days,
            args.state_days,
            args.max_data_mb,
        )
    print("%s %d 项" % ("已清理" if args.apply else "待清理", total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
