#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules_util.py —— rules.py 的通用辅助（常量 / 格式化 / 事件结构 / 配置读取）。

纯函数、不联网、不读写文件；拆出来是为了让 rules.py 专注规则判定并保持 <500 行。
"""
import datetime
import logging
import math
import time

LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

RULE_ORDER = ("fast_move", "day_move", "new_extreme", "volume_spike",
              "funding", "open_interest", "level_break", "data_quality")
SEV_RANK = {"P0": 0, "P1": 1, "P2": 2}
WINDOW_LABEL = {"m1": "1 分钟", "m5": "5 分钟", "m15": "15 分钟", "h1": "1 小时"}
WINDOW_SHORT = {"m1": "1m", "m5": "5m", "m15": "15m", "h1": "1h"}
DEFAULT_MIN_SOURCES, DEFAULT_MAX_SPREAD_BPS, DEFAULT_STALE_SECONDS = 2, 50.0, 180
OI_KEEP_SECONDS, OI_MAX_ROWS = 24 * 3600, 2000

# (影响, 动作参考)：简短可解释，不构成投资建议。
_NOTE = {
    "fast_move:up": ("急涨常伴随空头回补与杠杆放大，波动可能继续放大", "检查杠杆仓位与追高风险，不要在急拉时追单"),
    "fast_move:down": ("急跌通常伴随杠杆清算，波动可能继续放大", "检查杠杆仓位与止损，不要在插针时追单"),
    "day_move:up": ("24h 累计涨幅较大，趋势资金可能顺势跟进", "关注能否站稳关键位，避免追高"),
    "day_move:down": ("24h 累计跌幅较大，市场情绪偏弱", "关注止跌信号与杠杆风险，避免盲目抄底"),
    "new_extreme:up": ("突破 24h 高点说明多头占优，可能吸引趋势资金", "关注能否站稳前高，注意假突破风险"),
    "new_extreme:down": ("跌破 24h 低点说明抛压主导，恐慌可能放大", "关注能否收回前低，注意下杀风险"),
    "volume_spike:up": ("放量上涨说明资金进场，方向确认前波动更大", "等放量后的方向确认，避免重仓追涨"),
    "volume_spike:down": ("放量下跌说明抛压集中释放，波动可能延续", "等缩量企稳再判断，避免接飞刀"),
    "funding:up": ("资金费率偏高说明多头拥挤，存在回调/清算风险", "降低杠杆，关注多空比与费率是否继续走高"),
    "funding:down": ("资金费率偏负说明空头拥挤，存在逼空风险", "降低杠杆，警惕空头挤压"),
    "open_interest:up": ("持仓量与价格同升，新资金顺势进场，趋势可能延续", "关注后续量价配合，避免在拥挤方向追高"),
    "open_interest:down": ("持仓量与价格同降，多头减仓离场，下跌可能延续", "关注是否出现缩量止跌，避免盲目抄底"),
    "level_break:up": ("整数关口常聚集止损与挂单，突破后容易加速", "关注突破是否放量确认，追单注意回落风险"),
    "level_break:down": ("整数关口失守易触发止损，下跌可能加速", "关注能否收回关口，注意恐慌下杀"),
    "data_quality:flat": ("价差/源数异常时价格可能失真，暂不据此判断行情", "等待数据恢复；如需交易请以单一主流交易所价格复核"),
}


def _num(value):
    """有限 float；非法值返回 None。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value):
    num = _num(value)
    return num if num is not None and num > 0 else None


def _required(cfg, key, rule):
    """读必需阈值；缺失返回 None 并记 warning（调用方跳过该规则）。"""
    value = _num((cfg or {}).get(key))
    if value is None:
        LOGGER.warning("%s 缺少阈值 %s", rule, key)
    return value


def _clock(now, snapshot=None):
    """(epoch 秒, ISO 时间)；now 支持 None / datetime / epoch 秒。"""
    if now is None:
        epoch = _num((snapshot or {}).get("ts_epoch"))
        epoch = int(epoch) if epoch is not None else int(time.time())
    elif isinstance(now, datetime.datetime):
        epoch = int(now.timestamp())
    else:
        num = _num(now)
        epoch = int(num) if num is not None else int(time.time())
    return epoch, datetime.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def _fmt(value, kind="num", digits=None):
    """kind: price（千分位，按量级取小数）/ pct（带符号百分比）/ bps / num。"""
    num = _num(value)
    if num is None:
        return "—"
    if kind == "price":
        return ("{:,.0f}" if abs(num) >= 1000 else "{:,.2f}" if abs(num) >= 1 else "{:,.4f}").format(num)
    if kind == "pct":
        return "{:+.{d}f}%".format(num, d=2 if digits is None else digits)
    if kind == "bps":
        return "{:.{d}f}bps".format(num, d=1 if digits is None else digits)
    return "{:,.{d}f}".format(num, d=2 if digits is None else digits)


def _price_unit(value):
    """价格 + USDT 单位，如 74,922.67 USDT。"""
    text = _fmt(value, "price")
    return text if text == "—" else text + " USDT"


def _signed_amount(value):
    """带符号金额（USDT），如 -120.33 / +76.90。"""
    num = _num(value)
    if num is None:
        return "—"
    sign = "+" if num > 0 else "-" if num < 0 else ""
    return sign + _fmt(abs(num), "price")


def _pct_unsigned(value, digits=2):
    num = _num(value)
    return "—" if num is None else "{:.{d}f}%".format(abs(num), d=digits)


def _move_text(price, reference):
    """(现价 - 前值) 的金额与百分比，如 -120.33 USDT（-0.16%）。"""
    current, base = _num(price), _num(reference)
    if current is None or not base:
        return "—"
    delta = current - base
    return "%s USDT，%s" % (_signed_amount(delta), _fmt(delta / base * 100.0, "pct"))


def _direction(change):
    num = _num(change)
    if num is None or num == 0:
        return "flat"
    return "up" if num > 0 else "down"


def _note(rule, direction):
    return _NOTE.get("%s:%s" % (rule, direction)) or _NOTE.get("%s:flat" % rule) or ("", "")


def _config(rules_cfg):
    """同时接受整份 rules.yaml 与其中的 rules 子字典。"""
    full = rules_cfg if isinstance(rules_cfg, dict) else {}
    rules = full.get("rules") if isinstance(full.get("rules"), dict) else full
    return rules, full.get("consensus") if isinstance(full.get("consensus"), dict) else {}


def _enabled(rules, rule):
    """规则缺省视为启用（data_quality 没有独立阈值段，只在 consensus 里配置）。"""
    cfg = rules.get(rule)
    if cfg is None:
        return True
    return bool(cfg.get("enabled", True)) if isinstance(cfg, dict) else False


def _asset_cfg(watchlist, asset):
    assets = watchlist.get("assets") if isinstance(watchlist, dict) else None
    if isinstance(assets, dict):
        return assets.get(asset) if isinstance(assets.get(asset), dict) else {}
    for entry in assets if isinstance(assets, list) else []:
        if isinstance(entry, dict) and entry.get("symbol") == asset:
            return entry
    return {}


def _section(snapshot, kind, asset):
    entry = (snapshot.get(kind) or {}).get(asset)
    return entry if isinstance(entry, dict) else {}


def _candles(snapshot, asset):
    rows = _section(snapshot, "candles", asset).get("rows")
    return rows if isinstance(rows, list) else []


def _urls(watchlist, asset):
    url = str(_asset_cfg(watchlist, asset).get("chart_url") or "").strip()
    return [url] if url else []


def _evidence(quote):
    """多源核验明细（cards/delivery 展示源名与价差）。"""
    sources = quote.get("sources") if isinstance(quote, dict) else None
    out = []
    for name, item in (sources if isinstance(sources, dict) else {}).items():
        price = _num(item.get("price")) if isinstance(item, dict) else _num(item)
        if price is not None and price > 0:
            out.append({"source": str(name), "price": price})
    return out


def _consensus_line(quality):
    bits = []
    if quality["n_sources"] is not None:
        bits.append("%d 源" % int(quality["n_sources"]))
    if quality["spread_bps"] is not None:
        bits.append("价差 %s" % _fmt(quality["spread_bps"], "bps"))
    return ("多源核验：" + " · ".join(bits)) if bits else ""


def _stats_line(snapshot, asset):
    stats = _section(snapshot, "stats", asset)
    bits = []
    if _num(stats.get("change_pct")) is not None:
        bits.append("24h %s" % _fmt(stats.get("change_pct"), "pct"))
    if _num(stats.get("high")) is not None and _num(stats.get("low")) is not None:
        bits.append("24h 区间 %s – %s" % (_fmt(stats.get("low"), "price"),
                                          _fmt(stats.get("high"), "price")))
    return " · ".join(bits)


def _emit(rule, asset, direction, severity, title, ts, magnitude, detail, metrics,
          snapshot=None, quality=None, watchlist=None, key=None):
    """统一事件结构（§9.4）；价格类传 snapshot/quality/watchlist 以补多源核验信息。"""
    why, action = _note(rule, direction)
    if quality is not None:
        metrics = dict(metrics, spread_bps=quality["spread_bps"], n_sources=quality["n_sources"])
        # 卡片层统一展示「多源核验：x/3 ...（异常源）」+ 价差，这里不再重复拼一行。
    source_health = (snapshot or {}).get("source_health") if isinstance(snapshot, dict) else {}
    primary_sources = (snapshot or {}).get("primary_sources") if isinstance(snapshot, dict) else []
    return {
        "rule": rule, "asset": asset, "direction": direction, "severity": severity,
        "title": title, "detail": [line for line in detail if line], "metrics": metrics,
        "dedupe_key": key or "price:%s:%s:%s" % (asset, rule, direction),
        "magnitude": round(float(magnitude or 0.0), 4), "force_send": False, "ts": ts,
        "urls": _urls(watchlist, asset) if watchlist else [], "why": why, "action": action,
        "evidence": _evidence(_section(snapshot, "quotes", asset)) if snapshot else [],
        "source_health": source_health or {},
        "primary_sources": primary_sources or [],
    }
