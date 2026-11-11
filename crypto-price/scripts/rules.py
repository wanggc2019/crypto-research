#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py —— 价格/衍生品告警规则引擎（纯函数：不联网、不读 .env、不写盘）。

契约见 docs/crypto-price/design.md §2、§5：
    evaluate(snapshot, state, rules_cfg, watchlist, now=None) -> list[event]
    advance_state(state, snapshot, now=None) -> new_state   # 不修改入参
阈值全部来自 rules_cfg；数据质量不达标时跳过价格类规则、仍评估 funding/open_interest，
全源不可用发 P0（force_send）；同一资产同一轮 fast_move 多窗口只报最强一条。
常量/格式化/事件结构等通用辅助见 rules_util.py。
"""
import copy

import indicators
from rules_util import (
    DEFAULT_MAX_SPREAD_BPS,
    DEFAULT_MIN_SOURCES,
    DEFAULT_STALE_SECONDS,
    LOGGER,
    OI_KEEP_SECONDS,
    OI_MAX_ROWS,
    RULE_ORDER,
    SEV_RANK,
    WINDOW_LABEL,
    WINDOW_SHORT,
    _asset_cfg,
    _candles,
    _clock,
    _config,
    _direction,
    _emit,
    _enabled,
    _fmt,
    _move_text,
    _note,
    _num,
    _pct_unsigned,
    _positive,
    _price_unit,
    _required,
    _section,
    _stats_line,
)


# ---------------------------------------------------------------- 数据质量
def _quality(quote, consensus, now_ts):
    price = _positive(quote.get("price"))
    spread = _num(quote.get("spread_bps"))
    n_sources = _num(quote.get("n_sources"))
    sources = quote.get("sources") if isinstance(quote.get("sources"), dict) else {}
    if n_sources is None and sources:
        n_sources = float(len(sources))
    min_sources = _num(consensus.get("min_sources"))
    max_spread = _num(consensus.get("max_spread_bps_p1"))
    stale_seconds = _num(consensus.get("stale_seconds"))
    min_sources = DEFAULT_MIN_SOURCES if min_sources is None else min_sources
    max_spread = DEFAULT_MAX_SPREAD_BPS if max_spread is None else max_spread
    stale_seconds = DEFAULT_STALE_SECONDS if stale_seconds is None else stale_seconds
    reasons = []
    if price is None:
        reasons.append("无可用报价")
    if n_sources is not None and n_sources < min_sources:
        reasons.append("可用源 %d < %d" % (int(n_sources), int(min_sources)))
    max_block = _num(consensus.get("max_spread_bps_block"))
    if spread is not None and max_block is not None and spread > max_block:
        reasons.append("三源价差 %s > %s（视为数据不可用）" % (
            _fmt(spread, "bps"), _fmt(max_block, "bps")))
    elif spread is not None and spread > max_spread:
        reasons.append("三源价差 %s > %s" % (_fmt(spread, "bps"), _fmt(max_spread, "bps")))
    stale = [name for name, item in sources.items() if isinstance(item, dict)
             and _num(item.get("ts")) is not None and now_ts - _num(item.get("ts")) > stale_seconds]
    if sources and len(stale) == len(sources):
        reasons.append("报价过期（>%ds）" % int(stale_seconds))
    return {"ok": not reasons, "price": price, "spread_bps": spread,
            "max_spread_bps": max_spread, "n_sources": n_sources, "reasons": reasons,
            "excluded_sources": quote.get("excluded_sources") or [],
            "raw_spread_bps": quote.get("raw_spread_bps")}


def _sources_outage(snapshot):
    ok, total = _num(snapshot.get("sources_ok")), _num(snapshot.get("sources_total"))
    return ok is not None and total is not None and total > 0 and ok <= 0


def _quality_events(snapshot, assets, qualities, ts):
    """全源不可用 → 单条 P0（force_send）；单资产数据异常 → P1。"""
    why, action = _note("data_quality", "flat")
    if _sources_outage(snapshot):
        total = int(_num(snapshot.get("sources_total")) or 0)
        errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else []
        detail = ["可用源 0/%d" % total, "本轮跳过全部价格类规则，仅评估衍生品指标"]
        snippets = [(item.get("error") if isinstance(item, dict) else item) for item in errors[:3]]
        snippets = [str(text)[:60] for text in snippets if text]
        if snippets:
            detail.append("错误 %d 条：%s" % (len(errors), "；".join(snippets)))
            joined = " ".join(snippets).lower()
            if "certificate" in joined or "ssl" in joined:
                detail.append("提示：TLS 证书校验失败，通常是 VPN/代理证书拦截或系统根证书异常")
        event = _emit("data_quality", "ALL", "flat", "P0", "行情全源不可用，监控中断", ts, 100.0,
                      detail, {"sources_ok": 0, "sources_total": total, "errors": len(errors)},
                      snapshot=snapshot, key="price:ALL:data_quality:flat")
        event.update({"why": why, "action": action, "force_send": True})
        return [event]
    # 把「原因完全相同」的多资产异常合并成一条，避免 BTC/ETH/BNB 三连重复卡片。
    groups = {}
    for asset in assets:
        quality = qualities[asset]
        if quality["ok"]:
            continue
        key = tuple(quality["reasons"])
        groups.setdefault(key, []).append((asset, quality))
    affected = [(asset, quality) for _, items in groups.items() for asset, quality in items]
    if not affected:
        return []
    reasons_by_asset = {asset: list(quality["reasons"]) for asset, quality in affected}
    max_spread = max((q.get("spread_bps") or 0.0) for _, q in affected)
    min_sources = min((q.get("n_sources") or 0) for _, q in affected)

    # 多资产同时异常（即使原因不同）也只发一条，避免 BTC/ETH/BNB 三连刷屏。
    if len(affected) >= 2:
        assets = [asset for asset, _ in affected]
        asset_label = "/".join(assets)
        detail = ["受影响：%s" % "、".join(assets)]
        detail += ["%s：%s" % (asset, "；".join(reasons_by_asset[asset])) for asset in assets]
        detail.append("价格类规则本轮跳过")
        event = _emit("data_quality", asset_label, "flat", "P1",
                      "%s 行情数据异常（多资产）" % asset_label, ts, max_spread, detail,
                      {"spread_bps": max_spread, "n_sources": min_sources,
                       "assets": assets, "reasons_by_asset": reasons_by_asset},
                      snapshot=snapshot,
                      key="price:data_quality:multi:%s" % "-".join(sorted(assets)))
        event.update({"why": why, "action": action})
        return [event]

    asset, quality = affected[0]
    reasons = reasons_by_asset[asset]
    event = _emit("data_quality", asset, "flat", "P1",
                  "%s 行情数据异常：%s" % (asset, "；".join(reasons)), ts, max_spread,
                  list(reasons) + ["价格类规则本轮跳过"],
                  {"spread_bps": max_spread, "n_sources": min_sources,
                   "assets": [asset], "reasons": reasons},
                  snapshot=snapshot, key="price:%s:data_quality:flat" % asset)
    event.update({"why": why, "action": action})
    return [event]


# ---------------------------------------------------------------- 价格类规则
def _fast_move(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    del state  # 只用本轮 K 线
    cfg = rules.get("fast_move") or {}
    windows = cfg.get("windows") if isinstance(cfg.get("windows"), dict) else {}
    p1_map = cfg.get("p1_pct") if isinstance(cfg.get("p1_pct"), dict) else {}
    thresholds = p1_map.get(asset, p1_map.get("default"))
    if not isinstance(thresholds, dict):
        LOGGER.warning("fast_move 缺少资产 %s 的窗口阈值", asset)
        return None
    moves = indicators.window_moves(_candles(snapshot, asset), quality["price"], now_ts, windows)
    if not moves:
        return None
    multiplier = _num(cfg.get("p0_multiplier")) or 2.0
    floor_pct = _num(cfg.get("p0_floor_pct")) or 3.0
    candidates = []
    for name, threshold in thresholds.items():
        change, limit = moves.get(name), _num(threshold)
        if change is not None and limit is not None and limit > 0 and abs(change) >= limit:
            is_p0 = abs(change) >= max(limit * multiplier, floor_pct)
            # 先比等级（P0 优先），再比「超阈值倍数」，最后比绝对幅度。
            candidates.append((is_p0, abs(change) / limit, abs(change), name, change))
    if not candidates:
        return None
    is_p0, _, abs_change, name, change = max(candidates, key=lambda item: item[:3])
    direction = _direction(change)
    title = "%s %s急%s %.2f%%，现价 %s" % (asset, WINDOW_LABEL.get(name, name),
                                          "涨" if direction == "up" else "跌", abs_change,
                                          _price_unit(quality["price"]))
    short = " · ".join("%s %s" % (WINDOW_SHORT.get(key, key), _fmt(moves[key], "pct"))
                       for key in windows if moves.get(key) is not None)
    metrics = {"window": name, "change_pct": round(change, 2), "price": quality["price"],
               "windows": {key: round(value, 2) for key, value in moves.items()}}
    return _emit("fast_move", asset, direction, "P0" if is_p0 else "P1", title, ts, abs_change,
                 ["短周期：" + short, _stats_line(snapshot, asset)], metrics,
                 snapshot=snapshot, quality=quality, watchlist=watchlist)


def _day_move(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    del state, now_ts  # 24h 涨跌幅直接取行情统计
    cfg = rules.get("day_move") or {}
    stats = _section(snapshot, "stats", asset)
    change = _num(stats.get("change_pct"))
    if change is None:
        change = indicators.pct_change(quality["price"], stats.get("open"))
    if change is None:
        return None
    p1_map = cfg.get("p1_pct") if isinstance(cfg.get("p1_pct"), dict) else {}
    p0_map = cfg.get("p0_pct") if isinstance(cfg.get("p0_pct"), dict) else {}
    p1 = _num(p1_map.get(asset, p1_map.get("default")))
    if p1 is None:
        LOGGER.warning("day_move 缺少资产 %s 的 p1_pct", asset)
        return None
    p0 = _num(p0_map.get(asset, p0_map.get("default")))
    abs_change = abs(change)
    if abs_change < p1:
        return None
    direction = _direction(change)
    title = "%s 24 小时%s %.2f%%，现价 %s" % (
        asset, {"up": "上涨", "down": "下跌"}.get(direction, "异动"), abs_change,
        _price_unit(quality["price"]))
    metrics = {"change_pct": round(change, 2), "price": quality["price"],
               "high": _num(stats.get("high")), "low": _num(stats.get("low"))}
    return _emit("day_move", asset, direction,
                 "P0" if p0 is not None and abs_change >= p0 else "P1", title, ts, abs_change,
                 [_stats_line(snapshot, asset)], metrics,
                 snapshot=snapshot, quality=quality, watchlist=watchlist)


def _new_extreme(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    cfg = rules.get("new_extreme") or {}
    previous = (state.get("extremes") or {}).get(asset)
    if not isinstance(previous, dict):
        return None  # 首轮只初始化基线，不报（由 advance_state 建立）
    high, low = _num(previous.get("high")), _num(previous.get("low"))
    min_bps = _num(cfg.get("min_bps"))
    min_bps = 5.0 if min_bps is None else min_bps
    price = quality["price"]
    direction, reference = None, None
    if high is not None and high > 0 and price > high * (1.0 + min_bps / 10000.0):
        direction, reference = "up", high
    elif low is not None and low > 0 and price < low * (1.0 - min_bps / 10000.0):
        direction, reference = "down", low
    if direction is None:
        return None
    exceed_bps = abs(price / reference - 1.0) * 10000.0
    h1_move = indicators.window_moves(_candles(snapshot, asset), price, now_ts, {"h1": 3600}).get("h1")
    need = _num(cfg.get("p0_requires_h1_move_pct"))
    need = 2.5 if need is None else need
    is_p0 = h1_move is not None and _direction(h1_move) == direction and abs(h1_move) >= need
    title = "%s 创 24h %s %s USDT（超前值 %s）" % (
        asset, "新高" if direction == "up" else "新低", _fmt(price, "price"), _fmt(exceed_bps, "bps"))
    detail = ["前值 %s → 现价 %s（%s）" % (
        _price_unit(reference), _price_unit(price), _move_text(price, reference))]
    if h1_move is not None:
        detail.append("1h %s（P0 需同向 ≥%.1f%%）" % (_fmt(h1_move, "pct"), need))
    metrics = {"price": price, "previous": reference, "exceed_bps": round(exceed_bps, 2),
               "change_pct": round((price / reference - 1.0) * 100.0, 2),
               "h1_change_pct": round(h1_move, 2) if h1_move is not None else None}
    return _emit("new_extreme", asset, direction, "P0" if is_p0 else "P1", title, ts,
                 exceed_bps, detail, metrics, snapshot=snapshot, quality=quality, watchlist=watchlist)


def _volume_spike(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    del state
    cfg = rules.get("volume_spike") or {}
    window_min = int(_num(cfg.get("window_min")) or 5)
    baseline_min = int(_num(cfg.get("baseline_min")) or 55)
    candles = _candles(snapshot, asset)
    volume = indicators.volume_ratio(candles, now_ts, window_min, baseline_min)
    if volume is None:
        return None
    move = indicators.window_moves(candles, quality["price"], now_ts, {"w": window_min * 60}).get("w")
    p1_ratio = _required(cfg, "p1_ratio", "volume_spike")
    min_move = _required(cfg, "min_move_pct", "volume_spike")
    if move is None or p1_ratio is None or min_move is None:
        return None
    abs_move = abs(move)
    if volume["ratio"] < p1_ratio or abs_move < min_move:
        return None
    p0_ratio, p0_min_move = _num(cfg.get("p0_ratio")), _num(cfg.get("p0_min_move_pct"))
    is_p0 = (p0_ratio is not None and volume["ratio"] >= p0_ratio
             and p0_min_move is not None and abs_move >= p0_min_move)
    direction = _direction(move)
    title = "%s %d 分钟放量 %.1f 倍，价格%s %.2f%%，现价 %s" % (
        asset, window_min, volume["ratio"], {"up": "上涨", "down": "下跌"}.get(direction, "异动"),
        abs_move, _price_unit(quality["price"]))
    detail = ["近 %d 分钟每分钟均量 %s / 基线 %s（%.1f 倍）" % (
                  window_min, _fmt(volume["window_volume"], "num"),
                  _fmt(volume["baseline_avg"], "num"), volume["ratio"]),
              "价格 %d 分钟 %s" % (window_min, _fmt(move, "pct"))]
    metrics = {"ratio": round(volume["ratio"], 2), "window_volume": round(volume["window_volume"], 4),
               "baseline_avg": round(volume["baseline_avg"], 4), "window_min": window_min,
               "change_pct": round(move, 2), "price": quality["price"]}
    return _emit("volume_spike", asset, direction, "P0" if is_p0 else "P1", title, ts,
                 volume["ratio"], detail, metrics,
                 snapshot=snapshot, quality=quality, watchlist=watchlist)


def _level_breaks(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    del now_ts
    confirm_bps = _required(rules.get("level_break") or {}, "confirm_bps", "level_break")
    previous = (state.get("last_prices") or {}).get(asset)
    previous_price = _num(previous.get("price")) if isinstance(previous, dict) else None
    levels = _asset_cfg(watchlist, asset).get("levels")
    price = quality["price"]
    if confirm_bps is None or previous_price is None or price is None or not isinstance(levels, list):
        return []
    events = []
    for raw_level in levels:
        level = _num(raw_level)
        if level is None or level <= 0:
            continue
        # 上穿/下破关口后至少 confirm_bps 才算确认（防插针）。
        direction = ("up" if previous_price < level and price >= level * (1.0 + confirm_bps / 10000.0)
                     else "down" if previous_price > level and price <= level * (1.0 - confirm_bps / 10000.0)
                     else None)
        if direction is None:
            continue
        measure_bps = abs(price / level - 1.0) * 10000.0
        level_text = _fmt(level, "num", 0)
        title = "%s %s关键位 %s USDT，现价 %s" % (
            asset, "突破" if direction == "up" else "跌破", level_text, _price_unit(price))
        detail = ["前值 %s → 现价 %s（%s，穿越 %s USDT）" % (
                      _price_unit(previous_price), _price_unit(price),
                      _move_text(price, previous_price), level_text),
                  "确认幅度 %s（%s，较关口%s %s USDT；阈值 %s / %s）" % (
                      _fmt(measure_bps, "bps"), _pct_unsigned(measure_bps / 100.0, 3),
                      "低" if price < level else "高",
                      _fmt(abs(price - level), "price"), _fmt(confirm_bps, "bps"),
                      _pct_unsigned(confirm_bps / 100.0, 2))]
        events.append(_emit(
            "level_break", asset, direction, "P1", title, ts, measure_bps, detail,
            {"level": level, "price": price, "previous": previous_price,
             "change_pct": round((price / previous_price - 1.0) * 100.0, 2),
             "confirm_bps": round(measure_bps, 2)},
            snapshot=snapshot, quality=quality, watchlist=watchlist,
            key="price:%s:level:%g:%s" % (asset, level, direction)))
    return events


# ---------------------------------------------------------------- 衍生品规则
def _funding(asset, snapshot, rules, watchlist, now_ts, ts):
    del now_ts
    cfg = rules.get("funding") or {}
    entry = _section(snapshot, "funding", asset)
    rate = _num(entry.get("rate"))
    if rate is None:
        return None
    rate_pct = rate * 100.0
    abs_pct = abs(rate_pct)
    p1_abs = _required(cfg, "p1_abs_pct", "funding")
    if p1_abs is None or abs_pct < p1_abs:
        return None
    p0_abs = _num(cfg.get("p0_abs_pct"))
    direction = _direction(rate_pct)
    annual_pct = rate_pct * 3 * 365  # 8h 一次，一天 3 次
    title = "%s 资金费率 %s（%s）" % (
        asset, _fmt(rate_pct, "pct", 3), "多头拥挤" if direction == "up" else "空头拥挤")
    detail = ["8h 资金费率 %s · 折算年化 %s" % (_fmt(rate_pct, "pct", 3), _fmt(annual_pct, "pct")),
              "标记价 %s" % _price_unit(entry.get("mark_price"))]
    metrics = {"rate": rate, "rate_pct": round(rate_pct, 4),
               "annualized_pct": round(annual_pct, 2), "mark_price": _num(entry.get("mark_price"))}
    return _emit("funding", asset, direction,
                 "P0" if p0_abs is not None and abs_pct >= p0_abs else "P1",
                 title, ts, abs_pct, detail, metrics, snapshot=snapshot, watchlist=watchlist)


def _open_interest(asset, quality, snapshot, state, rules, watchlist, now_ts, ts):
    cfg = rules.get("open_interest") or {}
    entry = _section(snapshot, "oi", asset)
    oi_now = _positive(entry.get("oi"))
    if oi_now is None:
        return None
    window_min = int(_num(cfg.get("window_min")) or 15)
    history = list(((state.get("oi_history") or {}).get(asset)) or [])
    sample_ts = _num(entry.get("ts"))
    history.append({"ts": now_ts if sample_ts is None else sample_ts, "oi": oi_now})
    change = indicators.oi_change_pct(history, now_ts, window_min)
    move = indicators.window_moves(_candles(snapshot, asset), quality["price"], now_ts,
                                   {"w": window_min * 60}).get("w")
    only_with = _required(cfg, "only_with_move_pct", "open_interest")
    p1_change = _required(cfg, "p1_change_pct", "open_interest")
    if change is None or move is None or only_with is None or p1_change is None:
        return None
    direction = _direction(change)
    if abs(move) < only_with or _direction(move) != direction or abs(change) < p1_change:
        return None
    p0_change = _num(cfg.get("p0_change_pct"))
    title = "%s %d 分钟持仓量%s %.2f%%，价格同步%s %.2f%%，现价 %s" % (
        asset, window_min, "增加" if direction == "up" else "减少", abs(change),
        {"up": "上涨", "down": "下跌"}.get(direction, "异动"), abs(move),
        _price_unit(quality["price"]))
    detail = ["%dm 持仓量 %s · 价格 %s" % (window_min, _fmt(change, "pct"), _fmt(move, "pct")),
              "当前 OI %s" % _fmt(oi_now, "num")]
    metrics = {"window_min": window_min, "oi_change_pct": round(change, 2),
               "price_change_pct": round(move, 2), "oi": oi_now, "price": quality["price"]}
    return _emit("open_interest", asset, direction,
                 "P0" if p0_change is not None and abs(change) >= p0_change else "P1",
                 title, ts, abs(change), detail, metrics, snapshot=snapshot, watchlist=watchlist)


# ---------------------------------------------------------------- state 容错
def _sanitize_state(state):
    """容错读 state：合法 JSON 但字段类型损坏时回退为空结构，避免每轮 traceback。"""
    if not isinstance(state, dict):
        return {}
    result = dict(state)
    for key in ("extremes", "last_prices"):
        value = result.get(key)
        result[key] = {
            str(asset): item for asset, item in (value.items() if isinstance(value, dict) else [])
            if isinstance(item, dict)
        }
    raw_history = result.get("oi_history")
    result["oi_history"] = {
        str(asset): [row for row in rows if isinstance(row, dict)]
        for asset, rows in (raw_history.items() if isinstance(raw_history, dict) else [])
        if isinstance(rows, list)
    }
    return result


# ---------------------------------------------------------------- 入口
def _event_sort_key(event):
    rule = event.get("rule")
    position = RULE_ORDER.index(rule) if rule in RULE_ORDER else len(RULE_ORDER)
    return (SEV_RANK.get(event.get("severity"), 9), position, event.get("asset") or "")


def evaluate(snapshot, state, rules_cfg, watchlist, now=None):
    """一轮行情快照 → 待送达事件列表（纯函数，不修改 snapshot/state）。"""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    state = _sanitize_state(state)
    rules, consensus = _config(rules_cfg)
    now_ts, ts = _clock(now, snapshot)
    watch_assets = watchlist.get("assets") if isinstance(watchlist, dict) else None
    if isinstance(watch_assets, dict):
        assets = [str(key) for key in watch_assets]
    else:
        assets = [str(entry.get("symbol")) for entry in watch_assets or []
                  if isinstance(entry, dict) and entry.get("symbol")]
        assets = assets or [str(key) for key in (snapshot.get("quotes") or {})]
    qualities = {asset: _quality(_section(snapshot, "quotes", asset), consensus, now_ts)
                 for asset in assets}
    outage = _sources_outage(snapshot)
    events = _quality_events(snapshot, assets, qualities, ts) if (
        outage or _enabled(rules, "data_quality")) else []
    price_assets = [] if outage else [asset for asset in assets if qualities[asset]["ok"]]
    for rule_name, handler in (("fast_move", _fast_move), ("day_move", _day_move),
                               ("new_extreme", _new_extreme), ("volume_spike", _volume_spike),
                               ("level_break", _level_breaks)):
        if not _enabled(rules, rule_name):
            continue
        for asset in price_assets:
            produced = handler(asset, qualities[asset], snapshot, state, rules, watchlist, now_ts, ts)
            events.extend(produced if isinstance(produced, list) else [produced] if produced else [])
    # 衍生品规则：不受现货价差阻断（缺行情/历史时自行跳过）。
    if _enabled(rules, "funding"):
        events.extend(event for event in (_funding(asset, snapshot, rules, watchlist, now_ts, ts)
                                          for asset in assets) if event)
    if _enabled(rules, "open_interest"):
        events.extend(event for event in (_open_interest(asset, qualities[asset], snapshot, state,
                                                         rules, watchlist, now_ts, ts)
                                          for asset in assets) if event)
    events.sort(key=_event_sort_key)
    return events


def advance_state(state, snapshot, now=None):
    """产出下一轮 state（新对象）：last_prices / 24h extremes / 24h OI 历史。"""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    new_state = copy.deepcopy(_sanitize_state(state))
    now_ts, _ = _clock(now, snapshot)
    quotes = snapshot.get("quotes") if isinstance(snapshot.get("quotes"), dict) else {}
    stats = snapshot.get("stats") if isinstance(snapshot.get("stats"), dict) else {}
    last_prices = dict(new_state.get("last_prices") or {})
    extremes = dict(new_state.get("extremes") or {})
    prices = {}
    for asset, quote in quotes.items():
        price = _positive((quote or {}).get("price"))
        if price is not None:
            prices[asset] = price
            last_prices[asset] = {"price": price, "ts": now_ts}
    for asset, price in prices.items():
        stat = stats.get(asset) if isinstance(stats.get(asset), dict) else {}
        previous = extremes.get(asset) if isinstance(extremes.get(asset), dict) else {}
        stat_high, stat_low = _positive(stat.get("high")), _positive(stat.get("low"))
        if stat_high is not None and stat_low is not None and stat_high < stat_low:
            stat_high = stat_low = price  # 脏数据兜底：高低倒挂时退化为现价
        high = max(stat_high, price) if stat_high is not None else max(
            value for value in (_num(previous.get("high")), price) if value is not None)
        low = min(stat_low, price) if stat_low is not None else min(
            value for value in (_num(previous.get("low")), price) if value is not None)
        extremes[asset] = {"high": high, "low": low, "ts": now_ts}

    cutoff = now_ts - OI_KEEP_SECONDS
    oi_history = {}
    for asset, entries in (new_state.get("oi_history") or {}).items():
        oi_history[asset] = [
            row for row in (entries if isinstance(entries, list) else [])
            if isinstance(row, dict) and _num(row.get("ts")) is not None and _num(row.get("ts")) >= cutoff
        ]
    oi = snapshot.get("oi") if isinstance(snapshot.get("oi"), dict) else {}
    for asset, entry in oi.items():
        oi_now = _positive(entry.get("oi")) if isinstance(entry, dict) else None
        if oi_now is None:
            continue
        sample_ts = _num(entry.get("ts"))
        sample_ts = now_ts if sample_ts is None else int(sample_ts)
        rows = [row for row in oi_history.get(asset, []) if _num(row.get("ts")) != sample_ts]
        rows.append({"ts": sample_ts, "oi": oi_now})
        rows.sort(key=lambda row: _num(row.get("ts")) or 0.0)
        oi_history[asset] = rows[-OI_MAX_ROWS:]

    new_state.update({"last_prices": last_prices, "extremes": extremes, "oi_history": oi_history})
    return new_state
