#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-futures —— 合约持仓结构（多空比）监控与告警，一轮的主流程。

用法：
    python3 scripts/run.py                    # 跑一轮，不推送（安全默认）
    python3 scripts/run.py --notify           # 跑一轮并推送（launchd 用）
    python3 scripts/run.py --dry-run --json   # 只打印，不写盘、不推送
    python3 scripts/run.py --fixtures <dir> --now <epoch>   # 离线回放（不联网）
    python3 scripts/run.py --test             # 发一条测试消息

一轮：补历史（冷启动 / 缺口）→ 取最近 12 个点 → 判过期 → engine.step（与 backtest 共用）
→ 卡片 + 历史参照 → 推送 → exports / last_run.json → 每日刷新 30 天缓存（首次顺带预热历史参照）。
退出码：0 成功（含只有 SKIP）/ 1 有真实错误但有产出 / 2 凭据或配置错误 / 3 全部接口真实失败。
"""
import argparse
import json
import os
import sys
import time

import backtest
import cards
import engine
import outputs
import reference
import series as S
from sources import Fetcher, FetchError

import common
from crypto_core.notify import check_credentials, send_message, send_signal

DAY = 86400


# ---------------------------------------------------------------- 状态读写
def _state_path(paths, name):
    return os.path.join(paths["state"], name)


def load_round_state(paths, assets):
    return {
        "series": {a: S.load(_state_path(paths, "history-%s.json" % a)) for a in assets},
        "engine": common.safe_json_load(_state_path(paths, "engine.json"), None) or engine.new_state(),
        "records": common.safe_json_load(_state_path(paths, "triggers.json"), []),
        "meta": common.safe_json_load(_state_path(paths, "fetch.json"), {}),
    }


def save_round_state(paths, st):
    for a, s in st["series"].items():
        S.dump(_state_path(paths, "history-%s.json" % a), s)
    common.safe_json_dump(_state_path(paths, "engine.json"), st["engine"])
    common.safe_json_dump(_state_path(paths, "triggers.json"), st["records"])
    common.safe_json_dump(_state_path(paths, "fetch.json"), st["meta"])


# ---------------------------------------------------------------- 历史：冷启动 / 缺口
def _fetch_range(fetcher, asset, symbol, metric, start, end):
    try:
        return fetcher.history(asset, symbol, metric, start, end)
    except FetchError as e:
        fetcher._note(asset, S.ENDPOINTS[metric], e)
        return None


def ensure_history(fetcher, st, app, now):
    """本地历史不足 7 天 → 回补 7 天（失败后 6 小时内不重试）；最新点落后 > 60 分钟 → 回补缺口。"""
    hist = app.get("history") or {}
    keep_s = float(hist.get("keep_days", 7.05)) * DAY
    retry_s = float(hist.get("backfill_retry_min", 360)) * 60
    gap_s = float(hist.get("gap_backfill_min", 60)) * 60
    attempts = st["meta"].setdefault("backfill_attempt", {})
    for asset, symbol in app["assets"].items():
        s = st["series"][asset]
        for metric in S.METRICS:
            first, last = S.earliest_ts(s, metric), S.latest_ts(s, metric)
            key = "%s|%s" % (asset, metric)
            if first is None or first > now - 7 * DAY + S.STEP:
                if now - float(attempts.get(key) or 0) < retry_s:
                    continue
                attempts[key] = now
                pts = _fetch_range(fetcher, asset, symbol, metric, int(now - keep_s), now)
            elif last < now - gap_s:
                pts = _fetch_range(fetcher, asset, symbol, metric, last + S.STEP, now)
            else:
                continue
            if pts:
                S.merge(s, metric, pts)


def fetch_recent(fetcher, st, app, now):
    """各指标最近 12 个 5m 点 + 资金费率 + 实时 OI；返回 (fundings, live_oi)。"""
    fundings, funding_ok = {}, st["meta"].setdefault("funding_ok", {})
    live_oi, oi_ok = {}, st["meta"].setdefault("oi_ok", {})
    for asset, symbol in app["assets"].items():
        for metric in S.METRICS:
            pts = fetcher.recent(asset, symbol, metric)
            if pts:
                S.merge(st["series"][asset], metric, pts)
        f = fetcher.funding(asset, symbol)
        if f:
            fundings[asset] = f
            funding_ok[asset] = now
        oi_ok.setdefault(asset, now)   # 新接口的宽限期：从第一次尝试起算 60 分钟，避免上线首轮失败就判失效
        o = fetcher.open_interest(asset, symbol)
        if o:
            live_oi[asset] = o
            oi_ok[asset] = now
    return fundings, live_oi


def funding_intervals(fetcher, st, cfg, fundings, now):
    """{symbol: 结算间隔小时}；从未取到时返回 None（卡片注明「按 8h 折算」）。

    每天取一次 fundingInfo；本轮任一资金费率越过 funding_extreme 的 P1 阈值时当轮强制刷新
    （Binance 往往在费率极端时临时缩短结算间隔，旧缓存会低估年化）。
    """
    meta = st["meta"]
    cached = meta.get("funding_info") or {}
    rc = ((cfg.get("rules") or {}).get("funding_extreme")) or {}
    p1 = float(rc.get("p1_abs_pct", 0.10))
    hot = rc.get("enabled", True) and any(abs(_float(f.get("rate")) or 0.0) * 100 >= p1 for f in fundings.values())
    if cached.get("date") != common.local_date(now) or hot:
        got = fetcher.funding_info()
        if got is not None:
            cached = {"date": common.local_date(now), "ts": now, "intervals": got}
            meta["funding_info"] = cached
    return cached.get("intervals") if "intervals" in cached else None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sample_rt(st, app, fundings, live_oi, intervals, now):
    """把本轮实时快照写入各资产 RT 序列（当前 5m 桶）：(资金费率, 标记价, 实时 OI, 结算间隔小时)。

    同一个桶被两轮采到（launchd 抖动 / 手动跑）时按字段合并：本轮取不到的字段保留上一轮的值，
    避免一次部分失败覆盖掉已有的完整样本。
    """
    ts = S.bucket(now)
    for asset, symbol in app["assets"].items():
        f, o = fundings.get(asset) or {}, live_oi.get(asset) or {}
        if not f and not o:
            continue
        hours = None if intervals is None else intervals.get(symbol, 8)
        new = (_float(f.get("rate")), _float(f.get("mark_price")), _float(o.get("oi")), hours)
        old = st["series"][asset].get(S.RT, {}).get(ts) or (None,) * 4
        S.merge(st["series"][asset], S.RT, {ts: tuple(n if n is not None else p for n, p in zip(new, old))})


def stale_metrics(all_series, app, now):
    limit = now - float((app.get("history") or {}).get("stale_min", 20)) * 60
    return {a: {m for m in S.ALL if (S.latest_ts(s, m) or 0) < limit} for a, s in all_series.items()}


# ---------------------------------------------------------------- 30 天缓存 + 历史参照预热
def _cache_path(paths, asset):
    return _state_path(paths, "cache30d-%s.json" % asset)


def _fetch_cache_metric(fetcher, asset, symbol, metric, now, c30):
    try:
        return fetcher.history(asset, symbol, metric, int(now - float(c30.get("days", 29.5)) * DAY), now)
    except FetchError as e:
        if e.code != -1130:
            raise
        return fetcher.history(asset, symbol, metric, int(now - float(c30.get("fallback_days", 29)) * DAY), now)


def refresh_cache(fetcher, st, app, cfg, paths, now):
    """每天一次（失败 6 小时后重试）：重拉近 29.5 天，覆盖写缓存；首次成功后用它回放预热历史参照。"""
    meta, c30 = st["meta"], app.get("cache30d") or {}
    today = common.local_date(now)
    if not c30.get("enabled", True) or meta.get("cache_date") == today \
            or now - float(meta.get("cache_attempt") or 0) < 6 * 3600:
        return None
    meta["cache_attempt"] = now
    cache = {}
    try:
        for asset, symbol in app["assets"].items():
            cache[asset] = S.empty()
            for metric in S.METRICS:
                S.merge(cache[asset], metric, _fetch_cache_metric(fetcher, asset, symbol, metric, now, c30))
    except FetchError as e:
        fetcher._note("*", "cache30d", e)
        return None
    for asset, s in cache.items():
        S.dump(_cache_path(paths, asset), s)
    meta["cache_date"] = today
    if not meta.get("warmed"):
        warm_cards, _, _ = backtest.replay(cache, cfg, end=now)
        reference.add_cards(st["records"], warm_cards, cache, source="warmup")
        meta["warmed"] = True
    reference.fill_outcomes(st["records"], cache)
    return cache


# ---------------------------------------------------------------- 卡片与推送
def deliver(card_list, st, app, cfg, fundings, args, env=None, sender=None):
    """渲染 + （可选）推送；返回 [{"card", "text", "sent", "error"}]。参照统计先算、再把本卡记入。"""
    ref_cfg = cfg.get("reference") or {}
    token = chat_id = None
    if args.notify and not args.dry_run:
        token, chat_id, problems = check_credentials(env if env is not None else common.load_env())
        if problems:
            raise RuntimeError("Telegram 凭据不完整：%s" % "；".join(problems))
    out = []
    for card in card_list:
        ref = reference.stats(st["records"], card["rule"], card["ts"], int(ref_cfg.get("lookback_days", 30)),
                              float(ref_cfg.get("horizon_hours", 24)), int(ref_cfg.get("min_samples", 3)))
        asset = card["asset"]
        ctx = cards.context(st["series"][asset], card["ts"], (fundings.get(asset) or {}).get("rate"),
                            realtime=card["rule"] in cards.RT_RULES)
        link = (app.get("links") or {}).get("trading_data", "").format(symbol=app["assets"][asset])
        text = cards.format_card(card, ctx, ref, link=link, simulated=bool(args.fixtures))
        item = {"card": card, "text": text, "sent": False, "error": ""}
        if token:
            ok, err = send_signal(token, chat_id, text, module=common.MODULE, severity=card["severity"],
                                  assets=[asset], key="%s|%s|%s" % (card["rule"], asset, card["ts"]),
                                  sender=sender, disable_preview=True)
            item["sent"], item["error"] = bool(ok), err
        reference.add_cards(st["records"], [card], st["series"], source="live")
        out.append(item)
    return out


# ---------------------------------------------------------------- 一轮
def run_round(args, app, cfg, paths, now, fetcher, env=None, sender=None):
    st = load_round_state(paths, app["assets"])
    ensure_history(fetcher, st, app, now)
    fundings, live_oi = fetch_recent(fetcher, st, app, now)
    sample_rt(st, app, fundings, live_oi, funding_intervals(fetcher, st, cfg, fundings, now), now)
    keep_from = now - float((app.get("history") or {}).get("keep_days", 7.05)) * DAY
    for s in st["series"].values():
        S.prune(s, keep_from)
    stale = stale_metrics(st["series"], app, now)
    catchup = float((app.get("history") or {}).get("catchup_min", 60)) * 60
    card_list, p2 = engine.step(st["series"], st["engine"], cfg, now, stale=stale, catchup_s=catchup)
    reference.fill_outcomes(st["records"], st["series"])
    delivered = deliver(card_list, st, app, cfg, fundings, args, env=env, sender=sender)
    if not args.dry_run:
        refresh_cache(fetcher, st, app, cfg, paths, now)
    reference.prune(st["records"], now)
    oi_rule = ((cfg.get("rules") or {}).get("oi_spike")) or {}
    down = outputs.endpoints_down(st["series"], st["meta"].get("funding_ok") or {}, now, app,
                                  oi_ok=(st["meta"].get("oi_ok") or {}) if oi_rule.get("enabled", True) else None)
    result = {
        "now": now, "requests": fetcher.requests, "errors": fetcher.errors, "stale": stale, "down": down,
        "cards": delivered, "p2": p2,
        "export": outputs.build_export(st["series"], fundings, stale, st["records"], now),
        "last_run": outputs.last_run(now, fetcher.requests, fetcher.errors, down, card_list),
    }
    if not args.dry_run:
        _persist(paths, st, result, now)
    return result


def _persist(paths, st, result, now):
    save_round_state(paths, st)
    common.safe_json_dump(os.path.join(paths["exports"], "latest.json"), result["export"])
    common.safe_json_dump(_state_path(paths, "last_run.json"), result["last_run"])
    raw = os.path.join(paths["raw"], common.local_date(now))
    outputs.append_jsonl(os.path.join(raw, "cards.jsonl"),
                         [dict(d["card"], sent=d["sent"], error=d["error"]) for d in result["cards"]])
    outputs.append_jsonl(os.path.join(raw, "p2.jsonl"), result["p2"])


def exit_code(result):
    errors = result["errors"]
    real = [e for e in errors if e["kind"] == "error"]
    if real and len(real) >= max(1, result["requests"]):
        return 3
    if real or any(d["error"] and not d["error"].startswith("suppressed") for d in result["cards"]):
        return 1
    return 0


def _summary(result):
    sent = sum(1 for d in result["cards"] if d["sent"])
    skips = sum(1 for e in result["errors"] if e["kind"] == "skip")
    return "requests=%d cards=%d sent=%d p2=%d skip=%d error=%d down=%s" % (
        result["requests"], len(result["cards"]), sent, len(result["p2"]), skips,
        len(result["errors"]) - skips, ",".join(sorted(result["down"])) or "-")


def send_test(env=None):
    token, chat_id, problems = check_credentials(env if env is not None else common.load_env())
    if problems:
        print("❌ " + "；".join(problems))
        return 2
    text = "🧪【crypto-futures】<b>测试消息</b>\n通道正常（这不是告警）。\n🏷 任务：%s" % common.MODULE
    ok, err = send_message(token, chat_id, text, meta={"module": common.MODULE, "kind": "test"})
    print("✅ 已发送" if ok else "❌ 发送失败：%s" % err)
    return 0 if ok else 3


def main(argv=None):
    parser = argparse.ArgumentParser(description="合约持仓结构（多空比）监控与告警")
    parser.add_argument("--once", action="store_true", help="跑一轮后退出（默认行为）")
    parser.add_argument("--notify", action="store_true", help="推送 Telegram（默认不推送）")
    parser.add_argument("--dry-run", action="store_true", help="不写盘、不推送")
    parser.add_argument("--fixtures", help="离线回放目录（不联网）")
    parser.add_argument("--now", type=int, help="指定当前时间（epoch 秒，回放用）")
    parser.add_argument("--data-dir", help="数据目录（默认 crypto-futures/data，测试用）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--test", action="store_true", help="发一条测试消息")
    args = parser.parse_args(argv)
    if args.test:
        return send_test()
    app, cfg = common.load_config("app.yaml"), common.load_config("rules.yaml")
    if not app.get("assets"):
        print("❌ config/app.yaml 缺少 assets")
        return 2
    paths = common.ensure_dirs(common.data_paths(args.data_dir))
    now = int(args.now if args.now is not None else time.time())
    fetcher = Fetcher(app, fixture_dir=args.fixtures, now=now)
    try:
        result = run_round(args, app, cfg, paths, now, fetcher)
    except RuntimeError as e:
        print("❌ %s" % e)
        return 2
    rc = exit_code(result)
    if args.json:
        view = {k: v for k, v in result.items() if k not in ("cards", "stale")}
        view["cards"] = [{"card": d["card"], "sent": d["sent"], "error": d["error"]} for d in result["cards"]]
        view["stale"] = {a: sorted(m) for a, m in result["stale"].items()}
        print(json.dumps(view, ensure_ascii=False, indent=1, default=str))
    else:
        for d in result["cards"]:
            print(d["text"] + "\n")
        common.log_line("done rc=%d %s" % (rc, _summary(result)), paths)
    return rc


if __name__ == "__main__":
    sys.exit(main())
