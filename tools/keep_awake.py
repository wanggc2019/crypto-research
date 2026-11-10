#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keep_awake.py —— 安装/查看/卸载「Mac 保持唤醒」LaunchAgent（caffeinate -s）。

只解决 **AC 电源下系统休眠** 导致 crypto-price 夜间漏采的问题：
- 用 `caffeinate -s`：仅在接电源时阻止系统睡眠；电池模式仍按系统设置休眠；
- 不修改 `pmset` 全局设置，不需要 sudo；
- 显示器仍可正常休眠（不设置 -d）。

用法：
    python3 tools/keep_awake.py --install
    python3 tools/keep_awake.py --status
    python3 tools/keep_awake.py --uninstall
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LABEL = "com.crypto-research.caffeinate"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = AGENTS_DIR / (LABEL + ".plist")
LOG_DIR = REPO / "data" / "logs"


def build_plist(repo=None):
    repo = Path(repo or REPO)
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-s"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(repo),
        "StandardOutPath": str(repo / "data" / "logs" / "caffeinate.out.log"),
        "StandardErrorPath": str(repo / "data" / "logs" / "caffeinate.err.log"),
    }


def _domain():
    return "gui/%d" % os.getuid()


def _launchctl(*args):
    try:
        return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None


def info():
    """返回 (loaded, running, detail)；只读。"""
    proc = _launchctl("print", "%s/%s" % (_domain(), LABEL))
    loaded = proc is not None and proc.returncode == 0
    try:
        pgrep = subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True, text=True, timeout=5)
        running = bool(pgrep.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        running = False
    detail = ""
    if loaded and proc is not None:
        for line in proc.stdout.splitlines():
            if "state =" in line or "pid =" in line:
                detail += line.strip() + " "
    return loaded, running, detail.strip()


def install(dry_run=False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    data = build_plist()
    if dry_run:
        print(plistlib.dumps(data).decode("utf-8"))
        return 0
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(data, f)
    # 已加载则先卸载，保证新 plist 生效。
    _launchctl("bootout", "%s/%s" % (_domain(), LABEL))
    proc = _launchctl("bootstrap", _domain(), str(PLIST_PATH))
    if proc is None or proc.returncode != 0:
        err = (proc.stderr or "").strip() if proc is not None else "launchctl 不可用"
        print("❌ 安装失败：%s" % err)
        return 1
    print("✅ 已安装并启动：%s" % LABEL)
    print("   caffeinate -s：仅 AC 电源下阻止系统睡眠；屏幕仍可休眠")
    return 0


def uninstall():
    _launchctl("bootout", "%s/%s" % (_domain(), LABEL))
    try:
        PLIST_PATH.unlink()
    except OSError:
        pass
    print("✅ 已卸载：%s" % LABEL)
    return 0


def status():
    loaded, running, detail = info()
    print("任务：%s" % LABEL)
    print("  已加载：%s" % ("是 ✅" if loaded else "否 ❌"))
    print("  caffeinate 进程：%s" % ("运行中 ✅" if running else "未运行"))
    if detail:
        print("  状态：%s" % detail)
    return 0 if loaded and running else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mac 保持唤醒（AC 电源）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="安装并启动 LaunchAgent")
    group.add_argument("--uninstall", action="store_true", help="卸载 LaunchAgent")
    group.add_argument("--status", action="store_true", help="查看状态")
    group.add_argument("--dry-run", action="store_true", help="只打印 plist，不安装")
    args = parser.parse_args(argv)
    if args.dry_run:
        return install(dry_run=True)
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return status()


if __name__ == "__main__":
    sys.exit(main())
