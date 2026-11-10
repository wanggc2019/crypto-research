#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_launchd.py —— 渲染/安装/卸载模块的 launchd 任务（支持多模板）。

模板放在 `<module>/deploy/launchd/<template>.plist.tmpl`，默认 `agent`（主任务）。
占位符：
    __LABEL__         launchd Label（自动生成：<prefix>.<module>[.<template>]）
    __MODULE__        模块目录名（如 crypto-news）
    __MODULE_DIR__    模块绝对路径
    __REPO_DIR__      仓库根绝对路径
    __LOG_DIR__       模块 data/logs 绝对路径
    __PYTHON__        仓库 .venv 的 python（不存在则当前解释器）
    __INTERVAL__      轮询秒数（默认 agent=600 / healthcheck=1800，可用 --interval 覆盖）

用法：
    python3 tools/render_launchd.py --module crypto-news --render
    python3 tools/render_launchd.py --module crypto-news --install
    python3 tools/render_launchd.py --module crypto-news --template healthcheck --install
    python3 tools/render_launchd.py --module crypto-news --status
    python3 tools/render_launchd.py --module crypto-news --template healthcheck --status
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT_HOME = Path.home()
LABEL_PREFIX = "com.crypto-research"
DEFAULT_INTERVALS = {"agent": 600, "healthcheck": 1800, "inspect": 14400}
# 历史 Label：仓库根时代 / crypto-new 时代；只装主任务时顺手清理旧任务，避免残留
LEGACY_LABELS = (
    "com.crypto-research",
    "com.crypto-research.crypto-new",
)


def label_for(module, template="agent"):
    base = "%s.%s" % (LABEL_PREFIX, module)
    return base if template == "agent" else "%s.%s" % (base, template)


def template_path(module, template="agent"):
    return REPO / module / "deploy" / "launchd" / (template + ".plist.tmpl")


def installed_path(label):
    return Path.home() / "Library" / "LaunchAgents" / (label + ".plist")


def python_bin():
    candidate = REPO / ".venv" / "bin" / "python"
    return str(candidate if candidate.exists() else Path(sys.executable))


def module_poll_seconds(module):
    """读取模块 config/app.yaml 的 poll_seconds；没有就返回 None（回退模板默认值）。"""
    try:
        app = REPO / module / "config" / "app.yaml"
        if not app.is_file():
            return None
        sys.path.insert(0, str(REPO / "libs" / "crypto_core" / "src"))
        from crypto_core import config as core_config

        value = int((core_config.load_yaml(app) or {}).get("poll_seconds") or 0)
        return value if value > 0 else None
    except (ImportError, OSError, ValueError, TypeError):
        return None


def render(module, template="agent", interval=None):
    tmpl = template_path(module, template)
    if not tmpl.is_file():
        raise SystemExit("缺少模板：%s" % tmpl)
    module_dir = REPO / module
    if interval is None:
        interval = module_poll_seconds(module) or DEFAULT_INTERVALS.get(template, 600)
    text = tmpl.read_text(encoding="utf-8")
    text = text.replace("__LABEL__", label_for(module, template))
    text = text.replace("__MODULE__", module)
    text = text.replace("__REPO_DIR__", str(REPO))
    text = text.replace("__HOME__", str(ROOT_HOME))
    text = text.replace("__MODULE_DIR__", str(module_dir))
    text = text.replace("__LOG_DIR__", str(module_dir / "data" / "logs"))
    text = text.replace("__PYTHON__", python_bin())
    text = text.replace("__INTERVAL__", str(int(interval or DEFAULT_INTERVALS.get(template, 600))))
    return text


def _launchctl(*args, check=False):
    return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, check=check)


def _bootout(label):
    _launchctl("bootout", "gui/%d/%s" % (os.getuid(), label))


def _cleanup_legacy():
    for legacy in LEGACY_LABELS:
        _bootout(legacy)
        stale = installed_path(legacy)
        try:
            stale.unlink()
        except FileNotFoundError:
            pass


def _print_status(module, template):
    label = label_for(module, template)
    proc = None
    for i in range(3):
        proc = _launchctl("print", "gui/%d/%s" % (os.getuid(), label))
        if proc.returncode == 0:
            break
        if i < 2:
            time.sleep(0.15 * (i + 1))
    if proc is None or proc.returncode != 0:
        print("状态：未加载 ❌（%s）" % label)
        return 1
    print("状态：已加载 ✅（%s）" % label)
    for line in proc.stdout.splitlines():
        if any(k in line for k in ("state =", "last exit", "run interval", "path =")):
            print("  " + line.strip())
    return 0


def install(module, template="agent", interval=None):
    label = label_for(module, template)
    target = installed_path(label)
    target.parent.mkdir(parents=True, exist_ok=True)
    if template == "agent":
        _cleanup_legacy()
    target.write_text(render(module, template, interval), encoding="utf-8")
    _bootout(label)
    result = _launchctl("bootstrap", "gui/%d" % os.getuid(), str(target))
    check = _launchctl("print", "gui/%d/%s" % (os.getuid(), label))
    if check.returncode != 0:
        print("❌ 加载失败，请检查 plist：%s" % target)
        if result.stderr:
            print(result.stderr.strip())
        return 1
    print("已安装并加载：%s" % target)
    _print_status(module, template)
    print("日志：%s" % (REPO / module / "data" / "logs"))
    return 0


def uninstall(module, template="agent"):
    label = label_for(module, template)
    _bootout(label)
    try:
        installed_path(label).unlink()
    except FileNotFoundError:
        pass
    if template == "agent":
        _cleanup_legacy()
    print("已卸载：%s" % label)
    return 0


def status(module, template="agent"):
    return _print_status(module, template)


def main(argv=None):
    parser = argparse.ArgumentParser(description="渲染/安装/卸载模块的 launchd 任务")
    parser.add_argument("--module", required=True, help="模块目录名，如 crypto-news")
    parser.add_argument("--template", default="agent", help="模板名（agent / healthcheck …，默认 agent）")
    parser.add_argument("--interval", type=int, default=None, help="轮询秒数（默认 agent=600，healthcheck=1800）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--render", action="store_true", help="只渲染到 stdout")
    group.add_argument("--install", action="store_true", help="渲染并安装/重载")
    group.add_argument("--status", action="store_true", help="查看状态")
    group.add_argument("--uninstall", action="store_true", help="停用并删除")
    args = parser.parse_args(argv)

    if not (REPO / args.module).is_dir():
        print("❌ 模块目录不存在：%s" % (REPO / args.module))
        return 2
    if args.render:
        print(render(args.module, args.template, args.interval), end="")
        return 0
    if args.install:
        return install(args.module, args.template, args.interval)
    if args.status:
        return status(args.module, args.template)
    if args.uninstall:
        return uninstall(args.module, args.template)
    return 2


if __name__ == "__main__":
    sys.exit(main())
