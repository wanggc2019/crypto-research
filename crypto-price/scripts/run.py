#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py —— crypto-price 主入口（一轮采集 → 指标 → 规则 → 送达 → 快照/日报）。

用法：
    python3 scripts/run.py                     # 跑一轮，只记录不推送（安全默认）
    python3 scripts/run.py --notify            # 跑一轮并推送（collect.sh/launchd 用）
    python3 scripts/run.py --dry-run --json    # 只预览，不写状态/不推送
    python3 scripts/run.py --fixtures DIR      # 离线回放原始行情响应
    python3 scripts/run.py --snapshot FILE     # 直接回放已归一化快照（测试用）
    python3 scripts/run.py --test              # 发一条静默 Telegram 测试消息
    python3 scripts/run.py --status [--json]   # 查看最近运行/冷却/压制状态
    python3 scripts/run.py --report [--dry-run]  # 立即生成/推送日报
    python3 scripts/run.py --loop 60           # 常驻模式（VPS/调试用，不配合 launchd）

退出码（与仓库约定一致）：0 成功 / 1 部分源失败或有发送失败 / 2 配置或凭据错误 /
3 全部行情源不可用 / 4 单实例锁被占用。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

import common

DATA_DIR_OVERRIDE = None


def _compact_snapshot(snapshot):
    """只保留体积可控的字段写 raw（不含 K 线），每轮约 1–2KB。"""
    out = {
        "ts": snapshot.get("ts"),
        "sources_ok": snapshot.get("sources_ok"),
        "sources_total": snapshot.get("sources_total"),
        "quotes": {},
        "stats": {},
        "funding": snapshot.get("funding") or {},
        "oi": snapshot.get("oi") or {},
        "errors": snapshot.get("errors") or [],
    }
    for asset, q in (snapshot.get("quotes") or {}).items():
        out["quotes"][asset] = {
            "price": q.get("price"),
            "spread_bps": q.get("spread_bps"),
            "n_sources": q.get("n_sources"),
            "sources": {src: v.get("price") for src, v in (q.get("sources") or {}).items()},
        }
    for asset, s in (snapshot.get("stats") or {}).items():
        out["stats"][asset] = {k: s.get(k) for k in ("high", "low", "open", "change_pct", "volume", "source")}
    return out


def _export_payload(snapshot, events, now_iso):
    assets = {}
    for asset, q in (snapshot.get("quotes") or {}).items():
        st = (snapshot.get("stats") or {}).get(asset) or {}
        funding = (snapshot.get("funding") or {}).get(asset) or {}
        oi = (snapshot.get("oi") or {}).get(asset) or {}
        assets[asset] = {
            "price": q.get("price"),
            "change_24h_pct": st.get("change_pct"),
            "high_24h": st.get("high"),
            "low_24h": st.get("low"),
            "volume_24h": st.get("volume"),
            "n_sources": q.get("n_sources"),
            "spread_bps": q.get("spread_bps"),
            "sources": {src: v.get("price") for src, v in (q.get("sources") or {}).items()},
            # 只增字段（crypto-onchain M2 归因用）：当前资金费率与持仓量；旧消费者忽略即可
            "funding_rate": funding.get("rate"),
            "funding_ts": funding.get("ts"),
            "oi": oi.get("oi"),
            "oi_ts": oi.get("ts"),
        }
    return {
        "schema": "price/snapshot/v1",
        "generated_at": now_iso,
        "source_module": "crypto-price",
        "assets": assets,
        "signals": [
            {
                "rule": e.get("rule"), "asset": e.get("asset"), "direction": e.get("direction"),
                "severity": e.get("severity"), "title": e.get("title"),
                "dedupe_key": e.get("dedupe_key"), "ts": e.get("ts"),
            }
            for e in (events or [])
        ],
        "source_health": {
            "ok": snapshot.get("sources_ok"),
            "total": snapshot.get("sources_total"),
            "errors": snapshot.get("errors") or [],
            "primary_sources": snapshot.get("primary_sources") or [],
            "primary": snapshot.get("source_health") or {},
        },
    }


def _validate_configs(cfg):
    """返回配置问题列表（空 = 通过）。"""
    problems = []
    if not (cfg.get("watchlist") or {}).get("assets"):
        problems.append("watchlist.yaml 缺少 assets")
    for key, filename in (("watchlist", "watchlist.yaml"), ("rules", "rules.yaml"), ("app", "app.yaml")):
        if not cfg.get(key):
            problems.append("缺少或无法解析 config/%s" % filename)
        elif common.schema_problem(cfg[key]) is not None:
            problems.append("config/%s：%s" % (filename, common.schema_problem(cfg[key])))
    rules = (cfg.get("rules") or {}).get("rules") or {}
    if not rules:
        problems.append("rules.yaml 缺少 rules 段")
    if "delivery" not in (cfg.get("rules") or {}):
        problems.append("rules.yaml 缺少 delivery 段")
    return problems


def _prices_brief(snapshot):
    parts = []
    for asset, q in (snapshot.get("quotes") or {}).items():
        st = (snapshot.get("stats") or {}).get(asset) or {}
        parts.append("%s %s(%s)" % (asset, _fmt(q.get("price")), _fmt_pct(st.get("change_pct"))))
    return " ".join(parts) or "—"


def _fmt(v):
    try:
        return "{:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v):
    try:
        return "{:+.2f}%".format(float(v))
    except (TypeError, ValueError):
        return "—"


def _primary_status(snapshot, app_cfg):
    """主源报价状态。

    返回 (primary_ok, primary_complete, missing):
    - primary_ok: 每个资产都有 >= min_primary_sources 个主源报价（数据质量底线）
    - primary_complete: 每个资产的全部主源都成功（任一主源失败 → 降级）
    - missing: {asset: [缺失的主源名]}
    """
    quality = (app_cfg or {}).get("quality") or {}
    min_primary = int(snapshot.get("min_primary_sources") or quality.get("min_primary_sources") or 2)
    missing = {}
    primary_ok = bool(snapshot.get("quotes"))
    complete = bool(snapshot.get("quotes"))
    for asset, quote in (snapshot.get("quotes") or {}).items():
        quote = quote or {}
        n_primary = int(quote.get("n_sources") or 0)
        missing_sources = list(quote.get("primary_missing") or [])
        if missing_sources:
            missing[asset] = missing_sources
            complete = False
        if n_primary < min_primary:
            primary_ok = False
    return primary_ok, complete, missing


def _validate_snapshot(snapshot, now):
    """校验/规整回放快照，坏数据抛 ValueError（由调用方转 rc=2）。"""
    if not isinstance(snapshot, dict):
        raise ValueError("快照根节点必须是 JSON object")
    for key in ("quotes", "stats", "candles", "funding", "oi"):
        if not isinstance(snapshot.get(key), dict):
            snapshot[key] = {}
    if not isinstance(snapshot.get("errors"), list):
        snapshot["errors"] = []
    for key in ("sources_ok", "sources_total"):
        try:
            snapshot[key] = int(snapshot.get(key) or 0)
        except (TypeError, ValueError):
            snapshot[key] = 0
    if not snapshot.get("sources_total"):
        snapshot["sources_total"] = max(snapshot["sources_ok"], len(snapshot["quotes"]) * 3)
    if not snapshot.get("ts"):
        snapshot["ts"] = common.ts_iso(now)
    if not snapshot.get("ts_epoch"):
        snapshot["ts_epoch"] = int(now.timestamp())
    return snapshot


def _load_snapshot(args, watchlist, app_cfg, now):
    """按优先级加载行情：--snapshot > --fixtures > 联网。"""
    if args.snapshot:
        with open(args.snapshot, "r", encoding="utf-8") as f:
            snap = json.load(f)
        return _validate_snapshot(snap, now), "snapshot"
    import sources  # 延迟导入：--status 等模式不需要抓取器
    # fixture 回放不传 now_ts：sources 用数据内最大时间戳，保证窗口计算可复现。
    snap = sources.fetch_snapshot(watchlist, app_cfg, fixture_dir=args.fixtures,
                                  now_ts=None if args.fixtures else int(now.timestamp()))
    return snap, "fixtures" if args.fixtures else "network"


def _deliver(events, rules_cfg, paths, app_cfg, now, notify, dry_run):
    """统一走 delivery；未开启 --notify 时用 dry-run 计算"会发什么"。"""
    import delivery
    env = common.load_env()
    return delivery.deliver(
        events, rules_cfg, paths["state"], env=env, now=now,
        dry_run=dry_run or not notify, poll_seconds=(app_cfg or {}).get("poll_seconds"),
    )


def _export_daily(paths, snapshot, events, now, now_iso):
    """更新 exports/daily-<date>.json（当日开/收/高/低 + 当日信号），供周报消费。"""
    date = common.today_str(now)
    path = os.path.join(paths["exports"], "daily-%s.json" % date)
    old = common.safe_json_load(path, {})
    old = old if isinstance(old, dict) else {}
    day = {
        "schema": "price/daily/v1",
        "source_module": "crypto-price",
        "date": date,
        "generated_at": now_iso,
        "assets": old.get("assets") if isinstance(old.get("assets"), dict) else {},
        "signals": old.get("signals") if isinstance(old.get("signals"), list) else [],
        "primary_sources": snapshot.get("primary_sources") or [],
        "source_health": snapshot.get("source_health") or {},
    }
    for asset, q in (snapshot.get("quotes") or {}).items():
        price = q.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        prev = day["assets"].get(asset) or {}
        try:
            open_price = float(prev.get("open"))
        except (TypeError, ValueError):
            open_price = price
        high = max(float(prev.get("high", price) or price), price)
        low = min(float(prev.get("low", price) or price), price)
        day["assets"][asset] = {
            "open": open_price, "close": price, "high": high, "low": low,
            "last_ts": now_iso, "n_sources": q.get("n_sources"),
            "spread_bps": q.get("spread_bps"),
        }
    seen = {}
    for item in day["signals"]:
        if isinstance(item, dict):
            seen[item.get("dedupe_key") or item.get("title")] = item
    for e in events or []:
        seen[e.get("dedupe_key") or e.get("title")] = {
            "rule": e.get("rule"), "asset": e.get("asset"), "severity": e.get("severity"),
            "title": e.get("title"), "dedupe_key": e.get("dedupe_key"), "ts": e.get("ts"),
        }
    day["signals"] = list(seen.values())[-200:]
    common.safe_json_dump(path, day)


def _write_outputs(snapshot, events, delivery_result, paths, now, app_cfg):
    now_iso = common.ts_iso(now)
    # raw：每轮紧凑快照
    raw_path = common.raw_file(paths, "snapshots.jsonl", common.today_str(now))
    common.append_jsonl(raw_path, [{
        "ts": now_iso,
        "snapshot": _compact_snapshot(snapshot),
        "events": [{"rule": e.get("rule"), "asset": e.get("asset"), "severity": e.get("severity"),
                    "dedupe_key": e.get("dedupe_key"), "title": e.get("title")} for e in events],
        "delivery": {
            "sent": len(delivery_result.get("sent") or []),
            "held": len(delivery_result.get("held") or []),
            "suppressed": len(delivery_result.get("suppressed") or []),
        },
    }])
    # exports：跨模块只读契约
    os.makedirs(paths["exports"], exist_ok=True)
    common.safe_json_dump(os.path.join(paths["exports"], "latest.json"),
                          _export_payload(snapshot, events, now_iso))
    _export_daily(paths, snapshot, events, now, now_iso)
    return raw_path


def run_cycle(args, paths, cfg, now=None):
    """跑一轮，返回 {rc, result..., human_lines}。不打印（由 main 决定格式）。"""
    now = now or common.now()
    out = {"started_at": common.ts_iso(now), "events": [], "delivery": {}, "report": {},
           "rc": 0, "errors": []}
    lock = None
    if not args.dry_run and not args.no_lock:
        lock = common.acquire_lock(paths, stale_seconds=max(300, int((cfg.get("app") or {}).get("poll_seconds", 60)) * 5))
        if lock is None:
            out["rc"] = 4
            out["errors"].append("上一轮仍在运行（collect.lock 被占用），本轮跳过")
            return out
    try:
        try:
            snapshot, source_mode = _load_snapshot(args, cfg["watchlist"], cfg["app"], now)
        except (OSError, ValueError, TypeError) as e:
            out["rc"] = 2
            out["errors"].append("行情快照加载失败：%s: %s" % (type(e).__name__, e))
            out["finished_at"] = common.ts_iso(common.now())
            common.log_line("快照加载失败 rc=2：%s" % e)
            return out
        out["source_mode"] = source_mode
        out["snapshot"] = snapshot
        primary_ok, primary_complete, primary_missing = _primary_status(snapshot, cfg["app"])
        out["primary_ok"] = primary_ok
        out["primary_complete"] = primary_complete
        out["primary_missing"] = primary_missing
        import datetime

        import rules
        # 回放模式（fixtures/snapshot）用快照内时间，保证窗口计算可复现；实盘用当前时间。
        rule_now = now
        if source_mode in ("snapshot", "fixtures") and snapshot.get("ts_epoch"):
            try:
                rule_now = datetime.datetime.fromtimestamp(int(snapshot["ts_epoch"]))
            except (TypeError, ValueError, OSError):
                rule_now = now
        state = common.safe_json_load(os.path.join(paths["state"], "market_state.json"), {})
        # 全源不可用时 evaluate 自己会产出 P0 data_quality(force_send) 事件；这里只决定退出码。
        events = rules.evaluate(snapshot, state, cfg["rules"], cfg["watchlist"], now=rule_now)
        new_state = rules.advance_state(state, snapshot, now=rule_now)
        if not args.dry_run:
            common.safe_json_dump(os.path.join(paths["state"], "market_state.json"), new_state)
        if int(snapshot.get("sources_ok") or 0) <= 0:
            out["rc"] = 3
        elif not primary_ok:
            out["rc"] = 1  # 主源报价不足（< min_primary_sources），数据质量不保
        else:
            out["rc"] = 0  # 主源数量够；单个主源降级/次级源失败不告警
        events = sorted(
            events or [],
            key=lambda e: ({"P0": 0, "P1": 1, "P2": 2}.get(e.get("severity") or "P2", 9),
                           -float(e.get("magnitude") or 0)),
        )
        out["events"] = events
        delivery_result = _deliver(events, cfg["rules"], paths, cfg["app"], now,
                                   notify=args.notify, dry_run=args.dry_run)
        out["delivery"] = delivery_result
        if not args.dry_run:
            _write_outputs(snapshot, events, delivery_result, paths, now, cfg["app"])
        if delivery_result.get("errors"):
            out["errors"].extend(delivery_result["errors"])
            cred_error = any("凭据" in str(item.get("error", ""))
                             for item in delivery_result["errors"] if isinstance(item, dict))
            out["rc"] = max(out["rc"], 2 if cred_error else 1)

        # 日报（仅推送模式下真正执行；dry-run 只预览）
        if (cfg["app"].get("report") or {}).get("enabled"):
            import report
            out["report"] = report.maybe_run(
                paths["state"], snapshot, cfg["watchlist"], cfg["app"],
                env=common.load_env(), now=now, dry_run=args.dry_run or not args.notify,
            )
            if (out["report"] or {}).get("errors"):
                out["errors"].extend(out["report"]["errors"])
                out["rc"] = max(out["rc"], 1)
        if out["rc"] < 0:
            out["rc"] = 0
        out["finished_at"] = common.ts_iso(common.now())
        out["prices"] = {a: (q.get("price")) for a, q in (snapshot.get("quotes") or {}).items()}
        out["source_health"] = {
            "ok": snapshot.get("sources_ok"), "total": snapshot.get("sources_total"),
            "errors": (snapshot.get("errors") or [])[:20],
        }
        if not args.dry_run:
            _write_last_run(paths, out)
            common.log_line("轮次完成 rc=%s 事件=%d 推送=%d 压制=%d 源=%s/%s" % (
                out["rc"], len(out["events"]), len(delivery_result.get("sent") or []),
                len(delivery_result.get("held") or []),
                snapshot.get("sources_ok"), snapshot.get("sources_total"),
            ), paths)
        return out
    finally:
        if lock is not None:
            lock.release()


def _write_last_run(paths, out):
    payload = {
        "started_at": out.get("started_at"),
        "finished_at": out.get("finished_at"),
        "rc": out.get("rc"),
        "source_mode": out.get("source_mode"),
        "counts": {
            "events": len(out.get("events") or []),
            "sent": len((out.get("delivery") or {}).get("sent") or []),
            "held": len((out.get("delivery") or {}).get("held") or []),
            "suppressed": len((out.get("delivery") or {}).get("suppressed") or []),
            "sources_ok": (out.get("source_health") or {}).get("ok"),
            "sources_total": (out.get("source_health") or {}).get("total"),
        },
        "prices": out.get("prices"),
        "primary_ok": out.get("primary_ok"),
        "primary_complete": out.get("primary_complete"),
        "primary_missing": out.get("primary_missing") or {},
        "primary_sources": (out.get("snapshot") or {}).get("primary_sources") or [],
        "source_health": (out.get("snapshot") or {}).get("source_health") or {},
        "source_errors": (out.get("snapshot") or {}).get("errors") or [],
        "errors": out.get("errors") or [],
    }
    common.safe_json_dump(os.path.join(paths["state"], "last_run.json"), payload)


# ---------------------------------------------------------------- 输出
def _human_lines(out):
    lines = []
    d = out.get("delivery") or {}
    mode = "预览" if d.get("dry_run") else "实发"
    quiet = "静默中" if d.get("quiet") else "非静默"
    if not out.get("primary_ok"):
        primary = "主源不足"
    elif not out.get("primary_complete"):
        primary = "主源降级"
    else:
        primary = "主源完整"
    lines.append("[crypto-price] %s | %s | 源 %s/%s（%s） | 事件 %d → 推送 %d · 压制 %d · 归档 %d | %s %s" % (
        out.get("finished_at", "")[5:16].replace("T", " "),
        _prices_brief(out.get("snapshot") or {}),
        (out.get("source_health") or {}).get("ok", "—"),
        (out.get("source_health") or {}).get("total", "—"),
        primary,
        len(out.get("events") or []),
        len(d.get("sent") or []), len(d.get("held") or []), d.get("archived", 0),
        mode, quiet,
    ))
    for e in out.get("events") or []:
        lines.append("  %s %s %s" % (e.get("severity"), e.get("asset"), e.get("title")))
    for a in (d.get("sent") or [])[:5]:
        lines.append("  → 推送：%s %s" % (a.get("severity"), a.get("title")))
    for a in (d.get("held") or [])[:5]:
        lines.append("  → 压制：%s %s（%s）" % (a.get("severity"), a.get("title"),
                                            (a.get("delivery") or {}).get("reason", "")))
    for err in (out.get("errors") or [])[:5]:
        lines.append("  ! %s" % (err.get("error") if isinstance(err, dict) else err))
    source_errors = (out.get("snapshot") or {}).get("errors") or []
    if source_errors:
        grouped = {}
        for err in source_errors:
            if isinstance(err, dict):
                key = (err.get("source"), err.get("error"))
            else:
                key = ("unknown", str(err))
            grouped[key] = grouped.get(key, 0) + 1
        lines.append("  ! 源失败：%s（本轮已跳过，下一轮重试）" % "；".join(
            "%s/%s×%d" % (src, kind, count) for (src, kind), count in grouped.items()))
    return lines


def _healthcheck_line(out):
    h = out.get("source_health") or {}
    ok = h.get("ok") or 0
    total = h.get("total") or 0
    fail = max(0, total - ok) if total else len(h.get("errors") or [])
    return "采集完成：%d 个源，成功 %d，失败 %d，新增 %d 条" % (
        total, ok, fail, len(out.get("events") or []))


# ---------------------------------------------------------------- 子命令
def cmd_status(args, paths, cfg):
    import delivery
    delivery_state = delivery.load_state(paths["state"])
    report_state = common.safe_json_load(os.path.join(paths["state"], "report.json"), {})
    last = common.safe_json_load(os.path.join(paths["state"], "last_run.json"), {})
    if not isinstance(last, dict):
        last = {}
    alerts_today = common.safe_json_load(
        os.path.join(paths["state"], "alerts-%s.json" % common.today_str()), [])
    if not isinstance(alerts_today, list):
        alerts_today = []
    held = delivery.held_events(paths["state"])
    payload = {
        "module": "crypto-price",
        "job": common.job_name("crypto-price"),
        "assets": list(((cfg.get("watchlist") or {}).get("assets") or {}).keys()),
        "poll_seconds": (cfg.get("app") or {}).get("poll_seconds"),
        "quiet_hours": ((cfg.get("rules") or {}).get("delivery") or {}).get("quiet_hours"),
        "last_run": last,
        "counts_today": delivery_state.get("counts"),
        "cooldowns": len(delivery_state.get("cooldowns") or {}),
        "held": len(held),
        "alerts_today": len(alerts_today) if isinstance(alerts_today, list) else 0,
        "report_last_sent": (report_state or {}).get("last_sent_date"),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print("== crypto-price 状态 ==")
    print(" 任务：%s" % payload["job"])
    quiet_value = payload.get("quiet_hours")
    quiet_desc = ("关闭（全天即时）" if str(quiet_value).strip().lower() in ("", "off", "none", "false", "0")
                  else "%s（仅 P0 穿透）" % quiet_value)
    print(" 标的：%s ｜ 轮询：%ss ｜ 静默：%s" % (
        " / ".join(payload["assets"]), payload["poll_seconds"], quiet_desc))
    if last:
        print(" 最近一轮：%s rc=%s 事件=%s 推送=%s（%s）" % (
            last.get("finished_at"), last.get("rc"),
            (last.get("counts") or {}).get("events"), (last.get("counts") or {}).get("sent"),
            last.get("source_mode")))
        prices = last.get("prices") or {}
        if prices:
            print(" 现价：" + " · ".join("%s %s" % (a, _fmt(v)) for a, v in prices.items()))
    else:
        print(" 最近一轮：暂无记录（还没跑过 --notify 或 collect.sh）")
    counts = payload["counts_today"] or {}
    print(" 今日计数：P0 %s · P1 %s · P2 %s ｜ 冷却键：%d ｜ 夜间待汇总：%d ｜ 今日审计：%d 条 ｜ 日报：%s" % (
        counts.get("P0", 0), counts.get("P1", 0), counts.get("P2", 0),
        payload["cooldowns"], payload["held"],
        payload["alerts_today"], payload["report_last_sent"] or "未发送"))
    return 0


def _source_status_line(snapshot):
    import cards

    return cards.format_source_status(
        (snapshot or {}).get("source_health"), (snapshot or {}).get("primary_sources"))


def _test_message(snapshot, cfg=None):
    cfg = cfg or {}
    quotes = (snapshot or {}).get("quotes") or {}
    stats = (snapshot or {}).get("stats") or {}
    watch_assets = list(((cfg.get("watchlist") or {}).get("assets") or {}).keys())
    bits = []
    for asset, q in quotes.items():
        st = stats.get(asset) or {}
        bits.append("%s %s（%s）" % (asset, _fmt(q.get("price")), _fmt_pct(st.get("change_pct"))))
    h = (snapshot or {}).get("sources_ok")
    t = (snapshot or {}).get("sources_total")
    app = cfg.get("app") or {}
    quiet = ((cfg.get("rules") or {}).get("delivery") or {}).get("quiet_hours")
    if quiet is None:
        quiet = "22:00-07:00"
    quiet_desc = "关闭（全天即时）" if str(quiet).strip().lower() in ("", "off", "none", "false", "0") else "%s（仅 P0 穿透）" % quiet
    lines = [
        "✅【crypto-price】<b>测试消息</b>",
        "监控标的：%s" % (" / ".join(watch_assets) or "—"),
        "当前行情：%s" % ("｜".join(bits) if bits else "行情源暂不可用"),
        "多源核验：%s" % _source_status_line(snapshot),
        "数据源请求：%s/%s 成功" % (h if h is not None else "—", t if t is not None else "—"),
        "已启用规则：急涨急跌 / 24h 波动 / 新高新低 / 放量 / 资金费率 / 持仓量 / 关键位 / 数据质量",
        "轮询 %ss ｜ 静默 %s｜ 这条是静默通知测试，不会响铃。" % (
            app.get("poll_seconds", 60), quiet_desc),
    ]
    return "\n".join(lines)


def cmd_test(args, paths, cfg):
    now = common.now()
    try:
        snapshot, _ = _load_snapshot(args, cfg["watchlist"], cfg["app"], now)
    except Exception as e:  # noqa: BLE001 - 测试消息不应因行情失败而丢
        snapshot = {"quotes": {}, "stats": {}, "sources_ok": 0, "sources_total": 0, "errors": [str(e)]}
    env = common.load_env()
    from crypto_core.notify import check_credentials, send_message
    token, chat_id, problems = check_credentials(env)
    if problems:
        print("❌ Telegram 凭据不完整：%s" % "；".join(problems))
        return 2
    text = _test_message(snapshot, cfg)
    job = common.job_name("crypto-price")
    text += "\n🏷 任务：%s" % job
    if args.dry_run:
        print(text)
        print("\n（dry-run：未发送）")
        return 0
    ok, err = send_message(token, chat_id, text, True)
    if ok:
        print("✅ 测试消息已发送（静默）")
        return 0
    print("❌ 测试消息发送失败：%s" % err)
    return 3


def cmd_report(args, paths, cfg):
    now = common.now()
    import report
    try:
        snapshot, _ = _load_snapshot(args, cfg["watchlist"], cfg["app"], now)
    except Exception as e:  # noqa: BLE001
        print("❌ 行情获取失败，无法生成日报：%s" % e)
        return 3
    res = report.maybe_run(paths["state"], snapshot, cfg["watchlist"], cfg["app"],
                           env=common.load_env(), now=now, dry_run=args.dry_run, force=True)
    if args.dry_run:
        print(res.get("preview") or "")
        print("\n--- Telegram ---\n" + (res.get("telegram") or ""))
        return 0
    if res.get("generated"):
        print("✅ 日报已生成：%s%s" % (res.get("path"), "（已推送）" if res.get("sent") else ""))
    for err in res.get("errors") or []:
        print("⚠️ %s" % err)
    return 0 if res.get("generated") else 1


# ---------------------------------------------------------------- 模拟告警（完整链路，带测试标记）
def cmd_test_alert(args, cfg):
    """用离线冲击快照跑一遍「规则→分级→卡片→Telegram」，发送一条【模拟测试】告警。

    - 使用临时 data 目录，不写生产 state、不占真实冷却/计数；
    - 消息明确标注「模拟测试 / 不是真实行情」，避免和真实告警混淆；
    - --dry-run 只打印卡片，不发送。
    """
    import datetime

    import cards
    import delivery
    import rules

    now = common.now()
    fixture = args.snapshot or os.path.join(common.ROOT, "tests", "fixtures", "snapshot_shock.json")
    try:
        with open(fixture, "r", encoding="utf-8") as f:
            snapshot = _validate_snapshot(json.load(f), now)
    except (OSError, ValueError, TypeError) as e:
        print("❌ 模拟快照加载失败：%s: %s" % (type(e).__name__, e))
        return 2

    # 用最新真实快照补充「多源核验」状态，让测试消息也反映当前源健康（数据本身仍是回放）。
    if not args.snapshot:
        try:
            with open(os.path.join(common.ROOT, "data", "exports", "latest.json"),
                      "r", encoding="utf-8") as f:
                latest = json.load(f)
            health = (latest.get("source_health") or {})
            if health.get("primary"):
                snapshot["source_health"] = health["primary"]
            if health.get("primary_sources"):
                snapshot["primary_sources"] = health["primary_sources"]
        except (OSError, ValueError, TypeError):
            pass

    rule_now = datetime.datetime.fromtimestamp(int(snapshot["ts_epoch"]))
    events = rules.evaluate(snapshot, {}, cfg["rules"], cfg["watchlist"], now=rule_now)
    if not events:
        print("❌ 冲击快照没有触发任何规则（检查 tests/fixtures/snapshot_shock.json 与 rules.yaml）")
        return 1
    events.sort(key=lambda e: ({"P0": 0, "P1": 1, "P2": 2}.get(e.get("severity") or "P2", 9),
                               -float(e.get("magnitude") or 0)))
    target = events[0]
    target["simulated"] = True
    target["ts"] = common.ts_iso(now)  # 卡片用当前时间，数据仍是离线回放
    poll_seconds = (cfg.get("app") or {}).get("poll_seconds")
    alert = delivery.event_to_alert(target, now, poll_seconds=poll_seconds)

    if args.dry_run:
        print(cards.format_price_alert(alert))
        print("\n（dry-run：未发送）")
        return 0

    tmp = tempfile.mkdtemp(prefix="crypto-price-sim-")
    common.set_data_override(tmp)
    try:
        res = delivery.deliver(
            [target], cfg["rules"], os.path.join(tmp, "state"),
            env=common.load_env(), now=now, dry_run=False, poll_seconds=poll_seconds,
        )
    finally:
        common.set_data_override(args.data_dir)
        shutil.rmtree(tmp, ignore_errors=True)

    if res.get("errors"):
        print("❌ 模拟告警发送失败：%s" % "；".join(
            str(item.get("error", item)) for item in res["errors"]))
        return 1
    print("✅ 模拟告警已发送（消息内已标注【模拟测试】，不是真实行情）")
    print("   命中规则：%s · 级别：%s · 事件：%s" % (
        target.get("rule"), target.get("severity"), target.get("title")))
    return 0


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(description="crypto-price：BTC/ETH/BNB 价格异动实时告警")
    p.add_argument("--once", action="store_true", help="跑一轮后退出（默认行为）")
    p.add_argument("--notify", action="store_true", help="实际推送 Telegram（默认只记录）")
    p.add_argument("--dry-run", action="store_true", help="只预览：不写状态、不推送")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    p.add_argument("--quiet", action="store_true", help="正常时静默（异常仍输出）")
    p.add_argument("--fixtures", help="离线回放目录（原始行情响应，禁止联网）")
    p.add_argument("--snapshot", help="直接回放已归一化快照 JSON（测试用，禁止联网）")
    p.add_argument("--data-dir", help="覆盖 data/ 根目录（测试/回放用）")
    p.add_argument("--no-lock", action="store_true", help="不取单实例锁（测试用）")
    p.add_argument("--test", action="store_true", help="发送一条静默 Telegram 测试消息")
    p.add_argument("--test-alert", action="store_true",
                   help="用离线冲击快照模拟一条真实格式告警（带【模拟测试】标记，不碰生产状态）")
    p.add_argument("--status", action="store_true", help="查看最近运行/冷却/压制状态（不联网）")
    p.add_argument("--report", action="store_true", help="立即生成/推送日报")
    p.add_argument("--loop", type=int, default=0, help="常驻模式：每 N 秒跑一轮（默认关闭）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    paths = common.data_paths(args.data_dir)
    common.set_data_override(args.data_dir)
    cfg = common.load_all_configs()
    if (cfg.get("app") or {}).get("timezone"):
        common.apply_timezone(cfg["app"].get("timezone"))
    common.ensure_dirs(args.data_dir)
    problems = _validate_configs(cfg)
    if problems:
        print("❌ 配置错误：")
        for p in problems:
            print("  - %s" % p)
        return 2

    if args.status:
        return cmd_status(args, paths, cfg)
    if args.test:
        return cmd_test(args, paths, cfg)
    if args.test_alert:
        return cmd_test_alert(args, cfg)
    if args.report:
        return cmd_report(args, paths, cfg)

    now = common.now()
    out = run_cycle(args, paths, cfg, now=now)
    if args.json:
        summary = {
            "rc": out.get("rc"),
            "finished_at": out.get("finished_at"),
            "source_mode": out.get("source_mode"),
            "source_health": out.get("source_health"),
            "primary_ok": out.get("primary_ok"),
            "primary_complete": out.get("primary_complete"),
            "primary_missing": out.get("primary_missing"),
            "prices": out.get("prices"),
            "events": out.get("events"),
            "delivery": {
                "sent": [a.get("title") for a in (out.get("delivery") or {}).get("sent") or []],
                "held": [a.get("title") for a in (out.get("delivery") or {}).get("held") or []],
                "suppressed": [a.get("title") for a in (out.get("delivery") or {}).get("suppressed") or []],
                "archived": (out.get("delivery") or {}).get("archived", 0),
                "quiet": (out.get("delivery") or {}).get("quiet"),
                "dry_run": (out.get("delivery") or {}).get("dry_run"),
            },
            "report": out.get("report"),
            "errors": out.get("errors"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        if not args.quiet or out.get("rc") != 0:
            for line in _human_lines(out):
                print(line)
            print(_healthcheck_line(out))
        if out.get("rc") == 4:
            print("⚠️ 上一轮仍在运行，本轮跳过")
    if args.loop:
        interval = max(15, int(args.loop))
        try:
            while True:
                time.sleep(interval)
                out = run_cycle(args, paths, cfg, now=common.now())
                if not args.quiet or out.get("rc") != 0:
                    for line in _human_lines(out):
                        print(line)
                    print(_healthcheck_line(out))
                    sys.stdout.flush()
        except KeyboardInterrupt:
            print("已退出 --loop")
    return int(out.get("rc") or 0)


if __name__ == "__main__":
    sys.exit(main())
