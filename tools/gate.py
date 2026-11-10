#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate.py —— 仓库统一质量门（每次修改后跑一遍）。

用法：
    python3 tools/gate.py                 # 默认：离线全量（ruff + 全部单测 + 回放 E2E）
    python3 tools/gate.py --with-network  # 额外联网冒烟（外部源失败默认只警告）
    python3 tools/gate.py --strict-network# 联网冒烟失败也算失败
    python3 tools/gate.py --push-test     # 额外发一条静默 Telegram 测试消息（谨慎）
    python3 tools/gate.py --only libs,price  # 只跑部分步骤（调试用）

退出码：0 = 全部通过；1 = 有失败项；2 = 参数/环境错误。

设计原则：
- 默认完全离线、不推送、不写生产状态（E2E 用临时 data 目录）；
- 单步失败继续跑后面的步骤，最后汇总，避免“第一处失败就看不到全貌”；
- 每步打印耗时和失败输出尾部，便于直接定位；
- 通过时明确打印「✅ 测试通过」。
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
RUFF = os.path.join(REPO, ".venv", "bin", "ruff")
if not os.path.exists(RUFF):
    RUFF = shutil.which("ruff") or ""
LIBS = os.path.join(REPO, "libs", "crypto_core", "src")
BUILTIN_E2E = {"crypto-news", "crypto-price"}


def _env(extra=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = LIBS
    if extra:
        env.update(extra)
    return env


def _show(cmd):
    return " ".join(shlex.quote(str(part)) for part in cmd)


def run_step(name, cmd, cwd=None, env=None, timeout=600, tail=25):
    """跑一步，返回 (ok, seconds, output)。"""
    start = time.time()
    print("→ %-24s %s" % (name, _show(cmd)))
    try:
        proc = subprocess.run(cmd, cwd=cwd or REPO, env=env or _env(),
                              capture_output=True, text=True, timeout=timeout)
        ok = proc.returncode == 0
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        ok = False
        output = "TIMEOUT after %ss\n%s" % (timeout, (e.stdout or "") + (e.stderr or ""))
    except OSError as e:
        ok = False
        output = "OSError: %s" % e
    seconds = time.time() - start
    if ok:
        print("  ✅ %s（%.1fs）" % (name, seconds))
    else:
        print("  ❌ %s（%.1fs）" % (name, seconds))
        lines = output.strip().splitlines()
        for line in lines[-tail:]:
            print("     " + line)
    return ok, seconds, output


def gate_config():
    try:
        with open(os.path.join(REPO, "tools", "gate.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def discover_modules():
    """自动发现 crypto-* 模块（有 scripts/ 或 tests/ 即视为模块）。"""
    excluded = set(gate_config().get("exclude_modules") or [])
    modules = []
    for name in sorted(os.listdir(REPO)):
        path = os.path.join(REPO, name)
        if not name.startswith("crypto-") or not os.path.isdir(path):
            continue
        if name in excluded:
            continue
        if os.path.isdir(os.path.join(path, "scripts")) or os.path.isdir(os.path.join(path, "tests")):
            modules.append(name)
    return modules


def steps_offline():
    steps = []
    modules = discover_modules()
    lint_dirs = ["libs/crypto_core/src", "tools"]
    for module in modules:
        for sub in ("scripts", "tests"):
            rel = os.path.join(module, sub)
            if os.path.isdir(os.path.join(REPO, rel)):
                lint_dirs.append(rel)
    if RUFF:
        steps.append(("ruff", [RUFF, "check", "--no-cache"] + lint_dirs, None))
    steps.append(("libs 单测", [PY, "-m", "unittest", "discover",
                                "-s", os.path.join(REPO, "libs/crypto_core/tests")], None))
    steps.append(("tools 单测", [PY, "-m", "unittest", "discover",
                                 "-s", os.path.join(REPO, "tools/tests")], None))
    for module in modules:
        if not os.path.isdir(os.path.join(REPO, module, "tests")):
            continue
        steps.append(("%s 单测" % module, [PY, "-m", "unittest", "discover",
                                           "-s", os.path.join(REPO, module, "tests")], None))
    steps.append(("crypto-news 回放", [PY, os.path.join(REPO, "crypto-news/scripts/score.py")], None))
    # crypto-price 两个快照 E2E（临时 data 目录，不碰生产 state）
    tmp_price = tempfile.mkdtemp(prefix="gate-price-")
    for fixture in ("snapshot_normal", "snapshot_shock"):
        steps.append(("%s 回放" % fixture, [
            PY, os.path.join(REPO, "crypto-price/scripts/run.py"),
            "--snapshot", os.path.join(REPO, "crypto-price/tests/fixtures", fixture + ".json"),
            "--data-dir", tmp_price, "--dry-run", "--json",
        ], None))
    # 新模块可提供 scripts/e2e.py（退出码 0 即通过）；已有三个模块用内置回放。
    for module in modules:
        if module in BUILTIN_E2E:
            continue
        hook = os.path.join(REPO, module, "scripts", "e2e.py")
        if os.path.isfile(hook):
            steps.append(("%s e2e" % module, [PY, hook], None))
    # crypto-analyst 离线回放（日报 + 周报，fixtures）
    steps.append(("crypto-analyst 日报回放", [
        PY, os.path.join(REPO, "crypto-analyst/scripts/daily_brief.py"),
        "--fixtures", os.path.join(REPO, "crypto-analyst/tests/fixtures"),
        "--date", "2026-11-08", "--dry-run",
    ], None))
    steps.append(("crypto-analyst 周报回放", [
        PY, os.path.join(REPO, "crypto-analyst/scripts/weekly.py"),
        "--fixtures", os.path.join(REPO, "crypto-analyst/tests/fixtures"),
        "--date", "2026-11-08", "--dry-run",
    ], None))
    steps.append(("公开仓库信息检查", [PY, os.path.join(REPO, "tools/check_public_safe.py")], None))
    steps.append(("重复消息审计", [PY, os.path.join(REPO, "tools/audit_duplicates.py")], None))
    # launchd 任务健康：只有真失败（rc=2/3、未加载）才让门禁失败；
    # 降级（rc=1）与超时只打印（由 repo 级看门狗即时/每日告警），避免生产抖动卡住提交
    steps.append(("launchd 任务健康", [PY, os.path.join(REPO, "tools/jobs.py"), "--strict-errors"], None))
    return steps, [tmp_price]


def run_network(strict=False):
    """联网冒烟：真实行情 dry-run，检查代码能跑通 + 主源状态。"""
    tmp = tempfile.mkdtemp(prefix="gate-net-")
    cmd = [PY, os.path.join(REPO, "crypto-price/scripts/run.py"),
           "--data-dir", tmp, "--dry-run", "--json"]
    try:
        ok, seconds, output = run_step("联网 dry-run", cmd, tail=40)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not ok:
        print("  ⚠️ 网络冒烟命令失败（外部网络/交易所问题也会导致），默认只警告")
        return (not strict), seconds, output
    try:
        text = output.strip()
        start, end = text.find("{"), text.rfind("}")
        payload = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
        health = payload.get("source_health") or {}
        print("  源：%s/%s 成功；primary_ok=%s complete=%s" % (
            health.get("ok"), health.get("total"),
            payload.get("primary_ok"), payload.get("primary_complete")))
    except (ValueError, IndexError):
        print("  ⚠️ 无法解析 dry-run JSON，按失败处理")
        return False, seconds, output
    return True, seconds, output


def run_push_test():
    """发一条静默 Telegram 测试消息（只有显式 --push-test 才执行）。"""
    ok, seconds, output = run_step("Telegram 通道测试", [
        PY, os.path.join(REPO, "crypto-price/scripts/run.py"), "--test",
    ], tail=10)
    return ok, seconds, output


def main(argv=None):
    parser = argparse.ArgumentParser(description="仓库统一质量门（每次修改后跑一遍）")
    parser.add_argument("--with-network", action="store_true", help="额外联网冒烟（默认只警告外部失败）")
    parser.add_argument("--strict-network", action="store_true", help="联网冒烟失败也算失败（隐含 --with-network）")
    parser.add_argument("--push-test", action="store_true", help="额外发一条静默 Telegram 测试消息")
    parser.add_argument("--only", help="逗号分隔：libs,tools,news,price,jobs,network,push")
    args = parser.parse_args(argv)

    only = set((args.only or "").split(",")) if args.only else None
    started = time.time()
    results = []
    temps = []

    steps, temps = steps_offline()
    for name, cmd, _ in steps:
        key = ("libs" if name.startswith("libs") else
               "tools" if name.startswith("tools") else
               "news" if name.startswith("crypto-news") else
               "price" if name.startswith(("snapshot_", "crypto-price")) else
               "jobs" if name.startswith("launchd") else
               "duplicate" if "重复" in name else name)
        if only and key not in only:
            continue
        ok, seconds, _ = run_step(name, cmd)
        results.append((name, ok, seconds))

    if args.strict_network:
        args.with_network = True
    if args.with_network and (not only or "network" in only):
        ok, seconds, _ = run_network(strict=args.strict_network)
        results.append(("联网 dry-run", ok, seconds))
    if args.push_test and (not only or "push" in only):
        ok, seconds, _ = run_push_test()
        results.append(("Telegram 测试", ok, seconds))

    for path in temps:
        shutil.rmtree(path, ignore_errors=True)

    total = len(results)
    failed = [name for name, ok, _ in results if not ok]
    elapsed = time.time() - started
    print("\n" + "=" * 60)
    if failed:
        print("❌ 测试失败：%d/%d 步失败（%.1fs）" % (len(failed), total, elapsed))
        for name in failed:
            print("   - %s" % name)
        print("请修复后重新运行：python3 tools/gate.py")
        return 1
    print("✅ 测试通过：%d 步全绿（%.1fs）" % (total, elapsed))
    print("   提交前请再确认 git status 没有 data/、.env 等运行产物。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
