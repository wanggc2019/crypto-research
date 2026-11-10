#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 政策雷达入口：采集 → 规则门 → 法规库 → 速报 → 推送。

用法：
    python3 scripts/main.py                    # 跑一轮，只写产物，不推送（安全默认）
    python3 scripts/main.py --notify           # 跑一轮并推送新 P0/P1
    python3 scripts/main.py --dry-run          # 只打印，不写盘、不推送
    python3 scripts/main.py --test             # 发一条测试卡片
    python3 scripts/main.py --fixtures <dir>   # 离线回放（CI 用，禁止联网）
    python3 scripts/main.py --date 2026-11-08  # 指定处理日期

退出码：0 成功 / 1 部分源失败 / 2 配置或凭据错误 / 3 网络全挂 / 4 锁被占用。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief  # noqa: E402
import classify  # noqa: E402
import index as policy_index  # noqa: E402

import fetch  # noqa: E402
import notify  # noqa: E402
from common import (  # noqa: E402
    DATA_DIR,
    EXPORT_DIR,
    REPORT_DIR,
    STATE_DIR,
    ensure_dirs,
    in_quiet_hours,
    load_config,
    safe_json_dump,
    safe_json_load,
    ts_iso,
)

NOTIFIED_PATH = os.path.join(STATE_DIR, "notified.json")
PENDING_DIR = os.path.join(DATA_DIR, "pending")


def _load_notified():
    data = safe_json_load(NOTIFIED_PATH, {"ids": {}})
    if not isinstance(data, dict):
        data = {"ids": {}}
    data.setdefault("ids", {})
    return data


def _save_notified(data):
    safe_json_dump(NOTIFIED_PATH, data)


def _export(records, index, date):
    items = []
    for r in records:
        items.append(
            {
                "id": r.get("id"),
                "level": r.get("level"),
                "doc_type": r.get("doc_type"),
                "jurisdiction": r.get("jurisdiction") or "CN",
                "doc_no": r.get("doc_no"),
                "title": r.get("title"),
                "issuer": r.get("issuer"),
                "published_at": r.get("published_at"),
                "effective_at": r.get("effective_at"),
                "topics": r.get("topics") or [],
                "url": r.get("url"),
                "baseline": bool(r.get("baseline")),
            }
        )
    payload = {
        "schema": "policy/daily/v1",
        "date": date,
        "generated_at": ts_iso(),
        "items": items,
        "index_size": len(index.get("policies") or []),
    }
    safe_json_dump(os.path.join(EXPORT_DIR, "policy-%s.json" % date), payload)
    return payload


def _write_report(records, date):
    lines = ["# crypto-policy 速报 %s" % date, "", "> 机器筛选，未含法律解读；原文见 data/docs/。", ""]
    if not records:
        lines.append("本轮无新增相关政策/法规/案例。")
    order = {"P0": 0, "P1": 1, "P2": 2}
    for r in sorted(records, key=lambda x: order.get(x.get("level"), 9)):
        lines.append("## [%s] %s" % (r.get("level") or "P2", r.get("title") or ""))
        lines.append("")
        lines.append("- 类型：%s｜地区：%s｜文号：%s" % (
            r.get("doc_type_cn"), r.get("jurisdiction") or "CN", r.get("doc_no") or "—"))
        lines.append("- 机关：%s" % (r.get("issuer") or "—"))
        lines.append("- 发布/施行：%s / %s" % (r.get("published_at") or "—", r.get("effective_at") or "—"))
        lines.append("- 主题：%s" % (" · ".join(r.get("topics") or []) or "—"))
        if r.get("url"):
            lines.append("- 原文：%s" % r["url"])
        points = brief.pick_points(r.get("body") or "", r.get("topics") or [])
        if points:
            lines.append("- 要点：")
            for p in points:
                lines.append("  - %s" % p)
        lines.append("")
    path = os.path.join(REPORT_DIR, "%s.md" % date)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _defer_for_quiet(app):
    """是否因静默时段延迟推送；policy 默认 24/7 即时。"""
    delivery = app.get("delivery") or {}
    if delivery.get("ignore_quiet_hours"):
        return False
    return in_quiet_hours(span=delivery.get("quiet_hours") or brief.quiet_span())


def _is_stale(rec, days):
    """旧文件（>N 天或文号年份过旧）只入库，不推送/不解读。"""
    published = rec.get("published_at") or ""
    if published:
        try:
            y, m, d = [int(x) for x in published.split("-")[:3]]
            return (datetime.date.today() - datetime.date(y, m, d)).days > days
        except (ValueError, TypeError):
            pass
    m = re.search(r"〔(20\d{2})〕", (rec.get("doc_no") or "") + (rec.get("title") or ""))
    return bool(m) and int(m.group(1)) < datetime.date.today().year - 1


def _queue_pending(records, max_age_days):
    os.makedirs(PENDING_DIR, exist_ok=True)
    for r in records:
        if r.get("level") in ("P0", "P1") and not r.get("baseline") and not _is_stale(r, max_age_days):
            safe_json_dump(os.path.join(PENDING_DIR, r["id"] + ".json"), r)


def run(args):
    ensure_dirs()
    app = load_config("app.yaml")
    if not app.get("enabled", True):
        print("crypto-policy 已禁用（config/app.yaml enabled=false）")
        return 0

    res = fetch.run(app=app, fixtures=args.fixtures, only=args.only, limit=args.limit, dry_run=args.dry_run)
    total = len(res["stats"])
    ok = sum(1 for s in res["stats"] if s.get("list_ok"))
    print("采集完成：%d 个源，成功 %d，失败 %d" % (total, ok, total - ok))
    if res["stats"] and all(not s.get("list_ok") for s in res["stats"]):
        print("❌ 所有源都失败，检查网络或源配置")
        return 3

    records = [classify.build_record(item) for item in res["items"]]
    relevant = [r for r in records if classify.is_relevant(r)]
    for r in relevant:
        r["supersedes"] = classify.supersedes_of(r)

    date = args.date or ts_iso()[:10]
    max_age_days = int(app.get("push_max_age_days") or 540)
    report_path = ""
    if not args.dry_run:
        idx = policy_index.load()
        for r in relevant:
            policy_index.upsert(idx, r)
        policy_index.save(idx)
        _export(relevant, idx, date)
        report_path = _write_report(relevant, date)
        _queue_pending(relevant, max_age_days)

    notified = _load_notified()
    push = [
        r
        for r in relevant
        if r.get("level") in ("P0", "P1")
        and not r.get("baseline")
        and not _is_stale(r, max_age_days)
        and r.get("id") not in notified["ids"]
    ]
    # 跨源去重：同一文号（如最高法 + 公报）只推一次
    dedup, seen_keys = [], set()
    for r in push:
        key = r.get("doc_no") or r.get("title")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        dedup.append(r)
    push = dedup

    quiet = _defer_for_quiet(app)
    if quiet and not args.force:
        print("🌙 静默时段（%s），本轮只落盘，推送顺延" % brief.quiet_span())
        push = []

    if args.dry_run:
        print("[dry-run] 新条目 %d，相关 %d，待推送 %d" % (len(records), len(relevant), len(push)))
        for r in push:
            print("\n" + brief.render_card(r))
    elif args.notify and push:
        p0 = [r for r in push if r.get("level") == "P0"]
        p1 = [r for r in push if r.get("level") != "P0"]
        sent = 0
        for r in p0:
            ok, err = notify.send(brief.render_card(r))
            if ok:
                sent += 1
                notified["ids"][r["id"]] = ts_iso()
            else:
                print("❌ 推送失败：%s" % err)
        if p1:
            ok, err = notify.send(brief.render_digest(p1))
            if ok:
                sent += len(p1)
                for r in p1:
                    notified["ids"][r["id"]] = ts_iso()
            else:
                print("❌ 汇总推送失败：%s" % err)
        _save_notified(notified)
        print("✅ 已推送 %d 条（P0=%d, P1=%d）" % (sent, len(p0), len(p1)))
    else:
        flag = "已加 --notify" if args.notify else "未加 --notify"
        print("新条目 %d，相关 %d，待推送 %d（%s，只落盘）" % (len(records), len(relevant), len(push), flag))

    if res["errors"]:
        print("⚠️ 部分源失败：")
        for e in res["errors"][:5]:
            print("  - %s" % e)
    if report_path:
        print("产物：%s" % report_path)
    return 1 if res["errors"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-policy 政策雷达")
    parser.add_argument("--once", action="store_true", help="跑一轮后退出（默认行为）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘、不推送")
    parser.add_argument("--notify", action="store_true", help="推送新 P0/P1")
    parser.add_argument("--force", action="store_true", help="忽略静默时段")
    parser.add_argument("--test", action="store_true", help="发一条测试卡片")
    parser.add_argument("--fixtures", help="离线回放目录（CI 用，禁止联网）")
    parser.add_argument("--date", help="处理日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--only", help="只跑指定源（逗号分隔）")
    parser.add_argument("--limit", type=int, help="单源最多处理条数")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    if args.test:
        return notify.main(["--test"] + (["--dry-run"] if args.dry_run else []))
    try:
        return run(args)
    except Exception as e:  # noqa: BLE001 - 入口兜底，避免 launchd 静默失败
        print("❌ 运行失败：%s: %s" % (type(e).__name__, e))
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
