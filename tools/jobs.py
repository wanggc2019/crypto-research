#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs.py —— launchd 定时任务总览（**只读**，不改任何 plist、不触发任务）。

解决：活动监视器里所有任务都叫 bash，分不清谁是谁；单模块 `make launchd status`
只能看一个。本工具把仓库相关 LaunchAgent 汇总成一张表：

    make jobs            # 人类可读表格
    make jobs json=1     # 机器可读（JSON）
    make jobs strict=1   # 有失败/超时/未加载时退出码 1（可接 CI / 健康检查）

数据来源（全部只读）：
  1. ~/Library/LaunchAgents/<PREFIX>*.plist  —— Label / 频率 / 日志路径 / 程序
  2. launchctl print gui/<uid>/<label>      —— state / runs / last exit code / pid
  3. 日志文件 mtime                          —— 上次实际运行时间

退出码：0 正常输出；1 仅当 --strict 且存在问题；2 参数错误。
"""
import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PREFIX = "com.crypto-research."
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
WEEKDAY_CN = {0: "周日", 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


# ---------- 纯函数（可单测） ----------

def describe_schedule(plist):
    """把 StartInterval / StartCalendarInterval 翻译成中文频率描述。"""
    n = plist.get("StartInterval")
    if isinstance(n, int) and n > 0:
        if n % 3600 == 0:
            return f"每 {n // 3600} 小时", n
        if n % 60 == 0:
            return f"每 {n // 60} 分钟", n
        return f"每 {n} 秒", n
    cal = plist.get("StartCalendarInterval")
    if isinstance(cal, dict):
        cal = [cal]
    if isinstance(cal, list) and cal:
        parts = []
        for item in cal:
            if not isinstance(item, dict):
                continue
            wd = item.get("Weekday")
            hour = item.get("Hour", 0)
            minute = item.get("Minute", 0)
            day = item.get("Day")
            if wd is not None:
                text = f"每{WEEKDAY_CN.get(int(wd), wd)} {hour:02d}:{minute:02d}"
            elif day is not None:
                text = f"每月 {day} 日 {hour:02d}:{minute:02d}"
            else:
                text = f"每日 {hour:02d}:{minute:02d}"
            parts.append(text)
        if parts:
            # 日历任务的"预期周期"按最长间隔粗估：每日 1 天，每周 7 天
            period = 7 * 86400 if any(isinstance(i, dict) and i.get("Weekday") is not None for i in cal) else 86400
            return "、".join(parts), period
    return "手动/事件触发", None


def parse_print_output(text):
    """解析 `launchctl print gui/<uid>/<label>` 输出为 dict。"""
    out = {}
    for key, pattern, cast in (
        ("state", r"^\s*state = (.+)$", str),
        ("runs", r"^\s*runs = (\d+)$", int),
        ("last_exit_code", r"^\s*last exit code = (-?\d+)$", int),
        ("pid", r"^\s*pid = (\d+)$", int),
        ("program", r"^\s*program = (.+)$", str),
        ("stdout_path", r"^\s*stdout path = (.+)$", str),
        ("stderr_path", r"^\s*stderr path = (.+)$", str),
    ):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            try:
                out[key] = cast(m.group(1).strip())
            except ValueError:
                pass
    return out


def module_of(program_args, fallback="", working_dir=None):
    """推断模块目录名（如 crypto-analyst）。

    优先用 plist 的 WorkingDirectory（就是模块目录），其次从 ProgramArguments
    路径里找最具体的 crypto-*（排除仓库根目录 crypto-research）。
    """
    if working_dir:
        base = os.path.basename(os.path.normpath(str(working_dir)))
        if base.startswith("crypto-") and base != "crypto-research":
            return base
    matches = []
    for arg in program_args or []:
        matches += re.findall(r"(?<=/)(crypto-[a-zA-Z0-9-]+)(?=/)", str(arg))
    matches = [m for m in matches if m != "crypto-research"]
    if matches:
        return matches[-1]
    m = re.search(r"crypto-[a-zA-Z0-9-]+", fallback)
    return m.group(0) if m else fallback


def format_age(seconds):
    if seconds is None:
        return "未知"
    if seconds < 90:
        return f"{int(seconds)} 秒前"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400 * 2:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{int(seconds // 86400)} 天前"


def classify_health(info, last_run_ts, period_seconds, now):
    """返回 (状态文本, 严重级别)；级别: ok / warn / error / unknown。

    退出码语义按 docs/conventions.md §3：
      0=成功 → ok；1=部分源失败但有产出（降级）→ warn；
      4=单实例锁跳过（正常防重叠）→ ok；其它（2 配置/凭据、3 网络全挂）→ error。
    """
    if not info:
        return "⚠️ 未加载", "warn"
    if info.get("pid"):
        return "🟢 运行中", "ok"
    rc = info.get("last_exit_code")
    if rc == 4:
        return "🟢 正常（锁跳过 rc=4）", "ok"
    if rc == 1:
        return "🟠 降级(rc=1 部分源失败)", "warn"
    if rc not in (0, None):
        return f"🔴 失败(rc={rc})", "error"
    if last_run_ts is None:
        return "⚪ 无记录", "unknown"
    age = now - last_run_ts
    limit = max((period_seconds or 86400) * 2.5, 300)
    if age > limit:
        return f"🟠 超时({format_age(age)})", "warn"
    return "🟢 正常", "ok"


def discover(agents_dir=None, prefix=PREFIX):
    """扫描 LaunchAgents 目录，返回 job 列表（不调用 launchctl）。"""
    agents_dir = Path(agents_dir or AGENTS_DIR)
    jobs = []
    if not agents_dir.is_dir():
        return jobs
    for path in sorted(agents_dir.glob(f"{prefix}*.plist")):
        try:
            with open(path, "rb") as f:
                plist = plistlib.load(f)
        except Exception:
            continue
        label = plist.get("Label") or path.stem
        args = plist.get("ProgramArguments") or []
        freq_text, period = describe_schedule(plist)
        jobs.append({
            "label": label,
            "plist": str(path),
            "module": module_of(args, label, plist.get("WorkingDirectory")),
            "program": " ".join(str(a) for a in args[:3]),
            "frequency": freq_text,
            "period_seconds": period,
            "stdout_path": plist.get("StandardOutPath"),
            "stderr_path": plist.get("StandardErrorPath"),
            "working_dir": plist.get("WorkingDirectory"),
        })
    return jobs


# ---------- 运行期采集 ----------

def launchctl_info(label, attempts=3):
    """调用 launchctl print 获取运行状态；短暂失败会重试（任务重载时常见）。"""
    for i in range(attempts):
        try:
            proc = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0:
            return parse_print_output(proc.stdout)
        if i + 1 < attempts:
            time.sleep(0.15 * (i + 1))
    return {}


def last_run_ts(job):
    """最近一次运行时间的近似：stdout/stderr + 模块 data/logs/*.log 的 mtime 最大值。

    很多模块把日志重定向到自己的 collect.log，launchd 的 stdout 可能长期不变，
    因此必须同时看模块日志目录，否则会误报"超时"。
    """
    candidates = []
    for key in ("stdout_path", "stderr_path"):
        p = job.get(key)
        if p and os.path.exists(p):
            candidates.append(p)
    wd = job.get("working_dir")
    log_dir = os.path.join(wd, "data", "logs") if wd else None
    if log_dir and os.path.isdir(log_dir):
        candidates += [os.path.join(log_dir, n) for n in os.listdir(log_dir) if n.endswith(".log")]
    mtimes = []
    for p in candidates:
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def collect(agents_dir=None, prefix=PREFIX):
    jobs = discover(agents_dir, prefix)
    now = time.time()
    rows = []
    for job in jobs:
        info = launchctl_info(job["label"])
        # launchctl print 的日志路径优先（plist 缺省时兜底）
        for key in ("stdout_path", "stderr_path"):
            if not job.get(key) and info.get(key):
                job[key] = info[key]
        last = last_run_ts(job)
        status, level = classify_health(info, last, job["period_seconds"], now)
        rows.append({**job, **info, "status": status, "level": level,
                     "last_run_ts": last, "last_run_ago": format_age(now - last) if last else "未知"})
    return rows


# ---------- 输出 ----------

def render_table(rows):
    if not rows:
        return "没有发现 launchd 任务（目录：%s，前缀：%s）" % (AGENTS_DIR, PREFIX)
    head = ["状态", "模块", "Label", "频率", "上次运行", "退出", "runs", "日志"]
    table = [head]
    for r in rows:
        log = os.path.basename(r.get("stdout_path") or "") or "-"
        table.append([
            r["status"], r.get("module", ""), r["label"].replace(PREFIX, ""),
            r["frequency"], r.get("last_run_ago", "未知"),
            str(r.get("last_exit_code", "-")), str(r.get("runs", "-")), log,
        ])
    widths = [max(len(str(row[i])) for row in table) for i in range(len(head))]
    lines = []
    for idx, row in enumerate(table):
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
        if idx == 0:
            lines.append("  ".join("-" * widths[i] for i in range(len(head))))
    return "\n".join(lines)


def bad_rows(rows, errors_only=False):
    """筛出需要让 --strict 失败的行：默认 warn+error；errors_only 时只有真失败。"""
    if errors_only:
        return [r for r in rows if r.get("level") == "error"]
    return [r for r in rows if r.get("level") in ("warn", "error")]


def main(argv=None):
    ap = argparse.ArgumentParser(description="launchd 任务总览（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="存在失败/超时/未加载时退出 1（人工巡检用）")
    ap.add_argument("--strict-errors", action="store_true",
                    help="只有真失败（error 级）时退出 1；降级/超时只打印（质量门用）")
    ap.add_argument("--agents-dir", default=None, help="LaunchAgents 目录（测试用）")
    ap.add_argument("--prefix", default=PREFIX)
    args = ap.parse_args(argv)

    rows = collect(args.agents_dir, args.prefix)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"launchd 任务总览（只读）｜{time.strftime('%Y-%m-%d %H:%M')}｜目录 {args.agents_dir or AGENTS_DIR}")
        print(render_table(rows))
        print()
        print("提示：make jobs json=1 机器可读；make jobs strict=1 有问题时退出 1；"
              "make jobs errors=1 仅真失败才退出 1；"
              "状态含义 🟢正常/运行中 🟠降级/超时 🔴失败 ⚠️未加载 ⚪无记录")
    if args.strict or args.strict_errors:
        bad = bad_rows(rows, errors_only=args.strict_errors and not args.strict)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
