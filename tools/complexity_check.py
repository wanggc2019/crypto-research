#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""函数复杂度棘轮（ruff C901 / McCabe）：新代码不得超过上限，存量超标函数只许变简单、不许变复杂。

    python3 tools/complexity_check.py <dir> [<dir> ...]            # gate 调用：对比基线
    python3 tools/complexity_check.py --update <dir> [<dir> ...]   # 拆分函数后收紧基线

基线 tools/complexity_baseline.json：{"<相对路径>::<函数名>": 复杂度}，只记录 > MAX 的存量函数。
- 不在基线里的函数超过 MAX → 失败（新函数请拆小）；
- 基线里的函数复杂度上升 → 失败；
- 降低或消失 → 通过，并提示用 --update 收紧基线（不强制，避免一次重构牵连别的 PR）。
"""
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO, "tools", "complexity_baseline.json")
MAX = 15
_MSG = re.compile(r"`(?P<func>[^`]+)` is too complex \((?P<score>\d+) > \d+\)")


def _ruff():
    local = os.path.join(REPO, ".venv", "bin", "ruff")
    return local if os.path.exists(local) else (shutil.which("ruff") or "")


def measure(dirs, ruff=None):
    """返回 {"path::func": score}（只含 > MAX 的函数）。"""
    ruff = ruff or _ruff()
    if not ruff:
        raise RuntimeError("找不到 ruff（make setup 或 pip install ruff）")
    proc = subprocess.run(
        [ruff, "check", "--no-cache", "--select", "C901", "--config", "lint.mccabe.max-complexity=%d" % MAX,
         "--output-format", "json", "--exit-zero"] + list(dirs),
        cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ruff 执行失败：%s" % proc.stderr.strip())
    found = {}
    for item in json.loads(proc.stdout or "[]"):
        m = _MSG.search(item.get("message", ""))
        if not m:
            continue
        rel = os.path.relpath(item["filename"], REPO).replace(os.sep, "/")
        key = "%s::%s" % (rel, m.group("func"))
        found[key] = max(found.get(key, 0), int(m.group("score")))
    return found


def compare(current, baseline):
    """返回 (errors, improved)：errors 为失败原因列表；improved 为可收紧的基线项。"""
    errors, improved = [], []
    for key, score in sorted(current.items()):
        allowed = baseline.get(key)
        if allowed is None:
            errors.append("%s 复杂度 %d > %d（新超标函数，请拆分）" % (key, score, MAX))
        elif score > allowed:
            errors.append("%s 复杂度 %d > 基线 %d（存量函数只许变简单）" % (key, score, allowed))
        elif score < allowed:
            improved.append("%s %d → %d" % (key, allowed, score))
    for key in sorted(set(baseline) - set(current)):
        improved.append("%s %d → ≤%d（已达标）" % (key, baseline[key], MAX))
    return errors, improved


def load_baseline(path=BASELINE):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_baseline(data, path=BASELINE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(data.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    update = "--update" in argv
    dirs = [a for a in argv if a != "--update"] or ["."]
    missing = [d for d in dirs if not os.path.exists(os.path.join(REPO, d))]
    if missing:
        print("❌ 路径不存在：%s" % ", ".join(missing))
        return 2
    try:
        current = measure(dirs)
    except RuntimeError as exc:
        print("❌ %s" % exc)
        return 2
    if update:
        save_baseline(current)
        print("基线已更新：%d 个超标函数（上限 %d）" % (len(current), MAX))
        return 0
    errors, improved = compare(current, load_baseline())
    for line in errors:
        print("❌ " + line)
    if improved:
        print("可收紧基线（python3 tools/complexity_check.py --update <dirs>）：")
        for line in improved:
            print("   " + line)
    if errors:
        print("复杂度棘轮未通过：%d 项" % len(errors))
        return 1
    print("复杂度棘轮通过：存量超标 %d 个，无新增/恶化" % len(current))
    return 0


if __name__ == "__main__":
    sys.exit(main())
