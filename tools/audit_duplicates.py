#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_duplicates.py —— 重复消息/重复实例审计（纳入 make gate）。

检查两类真实重复问题：
1. **发送记录重复**：`data/state/alerts-<date>.json` 中同一 dedupe_key 在短时间内
   出现多条 `decision=sent` —— 说明去重/冷却/跨重启状态没生效。
2. **调度脚本重复**：`~/Library/LaunchAgents/com.crypto-research*.plist`
   中同一个 collect.sh/run.sh 被多个 Label 加载 —— 会产生两个进程同时发消息。

用法：
    python3 tools/audit_duplicates.py
    python3 tools/audit_duplicates.py --window-min 10 --json
退出码：0 未发现重复；1 发现重复（gate 会失败）。
"""
import argparse
import datetime
import glob
import json
import os
import plistlib
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL_PREFIX = "com.crypto-research"


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def sent_records(module):
    """读取模块审计文件里的 sent 记录：[(key, ts, title, path)]。"""
    out = []
    pattern = os.path.join(REPO, module, "data", "state", "alerts-*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue  # crypto-news 的 alerts-<date>.json 是 dict，无 delivery 字段
        for row in rows:
            if not isinstance(row, dict):
                continue
            delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
            if delivery.get("decision") not in ("send", "sent"):
                continue
            ts = _parse_ts(row.get("ts")) or _parse_ts(delivery.get("ts"))
            if ts is None:
                continue
            key = row.get("dedupe_key") or row.get("title") or ""
            reason = str(delivery.get("reason") or "")
            escalated = bool(row.get("escalated")) or any(
                word in reason for word in ("升级", "加剧", "幅度扩大", "等级提升"))
            out.append((str(key), ts, row.get("title") or "", path, reason, escalated))
    return out


def find_duplicate_sends(records, window_min=10):
    """同一 key 在 window 内被 sent 多次 → 重复发送。"""
    grouped = {}
    for key, ts, title, path, reason, escalated in records:
        grouped.setdefault(key, []).append((ts, title, path, reason, escalated))
    duplicates = []
    window = datetime.timedelta(minutes=window_min)
    for key, items in grouped.items():
        items.sort(key=lambda item: item[0])
        for previous, current in zip(items, items[1:]):
            if current[0] - previous[0] > window:
                continue
            # 冷却中因「幅度继续扩大」主动再报一次是设计行为（卡片带「波动加剧」），不算重复发送。
            if current[4]:
                continue
            duplicates.append({
                "key": key,
                "title": current[1],
                "first": previous[0].isoformat(timespec="seconds"),
                "second": current[0].isoformat(timespec="seconds"),
                "gap_seconds": int((current[0] - previous[0]).total_seconds()),
            })
    return duplicates


def launchd_duplicates(home=None):
    """同一执行脚本被多个 plist 加载 → 重复实例风险。"""
    folder = os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents")
    script_to_files = {}
    labels = []
    for path in sorted(glob.glob(os.path.join(folder, "*.plist"))):
        try:
            with open(path, "rb") as f:
                data = plistlib.load(f)
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        label = str(data.get("Label") or os.path.basename(path))
        if not label.startswith(LABEL_PREFIX):
            continue
        labels.append({"label": label, "path": os.path.basename(path)})
        for arg in data.get("ProgramArguments") or []:
            if isinstance(arg, str) and (arg.endswith("/collect.sh") or arg.endswith("/run.sh")):
                script_to_files.setdefault(arg, []).append(label)
    duplicates = [
        {"script": script, "labels": sorted(set(items))}
        for script, items in script_to_files.items() if len(set(items)) > 1
    ]
    return labels, duplicates


def main(argv=None):
    parser = argparse.ArgumentParser(description="重复消息/重复实例审计")
    parser.add_argument("--window-min", type=int, default=10, help="同一 key 的 sent 间隔窗口（分钟）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    modules = []
    for name in sorted(os.listdir(REPO)):
        path = os.path.join(REPO, name)
        if not name.startswith("crypto-") or not os.path.isdir(path):
            continue
        if glob.glob(os.path.join(path, "data", "state", "alerts-*.json")):
            modules.append(name)
    all_records = {m: sent_records(m) for m in modules}
    send_dups = {m: find_duplicate_sends(rows, args.window_min) for m, rows in all_records.items()}
    labels, script_dups = launchd_duplicates()

    failed = bool(script_dups) or any(send_dups.values())
    payload = {
        "modules": {m: {"sent": len(all_records[m]), "duplicates": send_dups[m]} for m in modules},
        "launchd": {"labels": labels, "duplicate_scripts": script_dups},
        "window_min": args.window_min,
        "ok": not failed,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("== 重复消息/重复实例审计 ==")
        for module in modules:
            print(" %-14s sent=%d 重复=%d" % (
                module, len(all_records[module]), len(send_dups[module])))
            for dup in send_dups[module]:
                print("   ❌ %s 在 %ss 内重复发送：%s" % (
                    dup["key"], dup["gap_seconds"], dup["title"][:60]))
        print(" launchd：%d 个任务" % len(labels))
        for item in labels:
            print("   - %s" % item["label"])
        for dup in script_dups:
            print("   ❌ 同一脚本被多个 Label 加载：%s → %s" % (
                dup["script"], "、".join(dup["labels"])))
        if failed:
            print(" 结论：发现重复发送/重复实例 ❌")
        else:
            print(" 结论：未发现重复发送/重复实例 ✅")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
