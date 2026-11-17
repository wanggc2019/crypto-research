#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cards.py —— 合约持仓结构告警的 Telegram HTML 卡片（docs/crypto-futures/requirement.md §5）。

区块顺序固定：标题 → 结论 → 指标 → 背景（合约价 · OI · 资金费率）→ 同时出现 → 解读 → 历史参照 → 口径 → 页脚。
用词约束：账户比只说「做多的账户占比」；大户持仓比下降只说「多空持仓比下降（可能减多，也可能加空）」，
不推断谁在开多、抄底或减仓，不给操作建议。
"""
import html

import indicators as ind

import common
from crypto_core.notify.telegram import job_label
from crypto_core.notify.templates import esc, truncate_html

MAX_CHARS = 600            # 可见文字上限（不含 HTML 标签）
ICON = {"P0": "🚨", "P1": "⚠️"}
RULE_CN = {
    "crowding_build": "拥挤加杠杆",
    "smart_divergence": "账户比与大户持仓比背离",
    "retail_shift": "账户比急转",
    "taker_burst": "合约主动买卖失衡",
    "extreme_level": "账户比创 7 天极值",
}
SCOPE = "Binance USD-M 单所 · 5m 数据"


# ---------------------------------------------------------------- 格式化
def _pct(v, digits=0):
    if v is None:
        return "—"
    return ("{:+.%df}%%" % digits).format(v).replace("-", "−")


def _price(v):
    if v is None:
        return "—"
    return "{:,.0f}".format(v) if abs(v) >= 10000 else "{:,.1f}".format(v)


def _usd_b(v):
    return "—" if v is None else "${:.2f}B".format(v / 1e9)


def _funding(rate):
    return "—" if rate is None else "{:.4f}%".format(rate * 100).replace("-", "−")


def _position(values, v):
    """7 天位置：最高 / 最低 / N% 分位；窗口为空返回空串。"""
    if not values or v is None:
        return ""
    if v >= max(values):
        return "7 天最高"
    if v <= min(values):
        return "7 天最低"
    return "7 天 %d%% 分位" % int(ind.rank_pct(values, v))   # 向下取整：不是最高就不显示 100%


# ---------------------------------------------------------------- 取数
def context(series, ts, funding_rate=None, points_7d=2016):
    """卡片需要的全部数值（缺失为 None）。"""
    return {
        "g": ind.value(series, "G", ts), "g_long": ind.value(series, "G", ts, 1),
        "g_1h": ind.value(series, "G", ts - ind.HOUR), "g_24h": ind.value(series, "G", ts - ind.DAY),
        "t": ind.value(series, "T", ts), "t_long": ind.value(series, "T", ts, 1),
        "t_24h": ind.value(series, "T", ts - ind.DAY),
        "g_window": ind.window(series, "G", ts, points_7d), "t_window": ind.window(series, "T", ts, points_7d),
        "price": ind.price(series, ts), "price_24h_pct": ind.price_change(series, ts, ind.DAY),
        "oi_usd": ind.value(series, "OI", ts, 1), "oi_4h_pct": ind.chg(series, "OI", ts, 4 * ind.HOUR, idx=1),
        "funding_rate": funding_rate,
    }


# ---------------------------------------------------------------- 各区块
def _g_line(ctx, span, with_position):
    ref = ctx["g_1h"] if span == "1h" else ctx["g_24h"]
    parts = ["账户多空比 %s（%s 前 %s，%s）" % (_num(ctx["g"]), span, _num(ref),
                                             _pct(ind.pct_change(ctx["g"], ref)))]
    if ctx["g_long"] is not None:
        parts.append("%d%% 账户做多" % round(ctx["g_long"] * 100))
    if with_position:
        pos = _position(ctx["g_window"], ctx["g"])
        if pos:
            parts.append(pos)
    return ("• " + " · ".join(parts)).replace("） · ", "）· ")


def _t_line(ctx, with_position):
    parts = ["大户持仓比 %s（24h 前 %s，%s）" % (_num(ctx["t"]), _num(ctx["t_24h"]),
                                            _pct(ind.pct_change(ctx["t"], ctx["t_24h"])))]
    if ctx["t_long"] is not None:
        parts.append("%d%% 仓位做多" % round(ctx["t_long"] * 100))
    if with_position:
        pos = _position(ctx["t_window"], ctx["t"])
        if pos:
            parts.append(pos)
    return ("• " + " · ".join(parts)).replace("） · ", "）· ")


def _k_line(metrics):
    return "• 主动买卖量比 %.2f（15 分钟：买 %s / 卖 %s）· 成交量为前 24h 中位数的 %.1f 倍" % (
        metrics["taker_ratio_15m"], "{:,.0f}".format(metrics["buy_vol"]),
        "{:,.0f}".format(metrics["sell_vol"]), metrics["volume_multiple"])


def _num(v):
    return "—" if v is None else "%.2f" % v


def _metric_lines(card, ctx):
    rule, metrics = card["rule"], card["hits"][0]["metrics"]
    if rule == "smart_divergence":
        return [_g_line(ctx, "24h", True), _t_line(ctx, False)]
    if rule == "crowding_build":
        return [_g_line(ctx, "1h", True), _t_line(ctx, True)]
    if rule == "taker_burst":
        return [_k_line(metrics), _g_line(ctx, "1h", False)]
    return [_g_line(ctx, "1h", False), _t_line(ctx, False)]


def _background(ctx):
    return "背景：合约价 %s（24h %s）· OI %s（4h %s）· 资金费率 %s" % (
        _price(ctx["price"]), _pct(ctx["price_24h_pct"], 1), _usd_b(ctx["oi_usd"]),
        _pct(ctx["oi_4h_pct"], 1), _funding(ctx["funding_rate"]))


def _trend(ctx):
    p = ctx["price_24h_pct"]
    if p is None or abs(p) < 3:
        return ""
    return "下跌中" if p < 0 else "上涨中"


CONCLUSION = {
    ("smart_divergence", "retail_long"): "24h 内做多账户占比明显上升，大户多空持仓比明显下降",
    ("smart_divergence", "retail_short"): "24h 内做多账户占比明显下降，大户多空持仓比明显上升",
    ("retail_shift", "up"): "1 小时内做多账户占比明显上升",
    ("retail_shift", "down"): "1 小时内做多账户占比明显下降",
}
READING = {
    ("smart_divergence", "retail_long"): "做多的账户变多了，大户的持仓却在往空的方向移（可能减多，也可能加空）。"
                                         "这只描述持仓结构，不代表接下来的方向。",
    ("smart_divergence", "retail_short"): "做多的账户变少了，大户的持仓却在往多的方向移（可能加多，也可能减空）。"
                                          "这只描述持仓结构，不代表接下来的方向。",
    ("crowding_build", "long"): "新增了大量合约持仓，做多的账户集中在一边，多头杠杆较集中。若价格反向波动，"
                                "多头被动平仓可能放大波动；这是风险提示，不是方向判断。",
    ("crowding_build", "short"): "新增了大量合约持仓，做空的账户集中在一边，空头杠杆较集中。若价格反向波动，"
                                 "空头被动平仓可能放大波动；这是风险提示，不是方向判断。",
    ("retail_shift", "up"): "做多的账户快速变多。说明账户层面的倾向变化很快，不说明这些账户的仓位大小，也不代表接下来的方向。",
    ("retail_shift", "down"): "做多的账户快速变少。说明账户层面的倾向变化很快，不说明这些账户的仓位大小，也不代表接下来的方向。",
    ("taker_burst", "buy"): "短时间内合约主动买入集中出现。只说明这 15 分钟的成交方向，不说明持仓方向，也不代表会持续。",
    ("taker_burst", "sell"): "短时间内合约主动卖出集中出现。只说明这 15 分钟的成交方向，不说明持仓方向，也不代表会持续。",
}


def _conclusion(card, ctx):
    key = (card["rule"], card["direction"])
    metrics = card["hits"][0]["metrics"]
    if card["rule"] == "crowding_build":
        level = _position(ctx["g_window"], ctx["g"])
        side = "高位" if card["direction"] == "long" else "低位"
        where = "到 " + level if level in ("7 天最高", "7 天最低") else "处在 7 天" + side
        return "做多账户占比%s，同时 4h 内 OI 增加 %.1f%%" % (where, metrics["oi_change_4h_pct"])
    if card["rule"] == "taker_burst":
        r = metrics["taker_ratio_15m"]
        if card["direction"] == "buy":
            return "15 分钟内合约主动买入量是卖出量的 %.1f 倍，成交量放大" % r
        return "15 分钟内合约主动卖出量是买入量的 %.1f 倍，成交量放大" % (1 / r)
    return CONCLUSION[key]


def reference_line(ref):
    if not ref or not ref.get("enough"):
        return "历史参照：样本不足（%d 次）" % ((ref or {}).get("n") or 0)
    return "历史参照：近 %d 天同规则（3 币）%d 次，之后 24h 涨 %d / 跌 %d，中位数 %s" % (
        ref["lookback_days"], ref["n"], ref["up"], ref["down"], _pct(ref["median"], 1))


# ---------------------------------------------------------------- 组装
def format_card(card, ctx, ref, link=None, job=None, simulated=False):
    """渲染一张卡片（card 为 engine.step 的输出，ctx 为 context()，ref 为 reference.stats()）。"""
    sev = card["severity"]
    lines = ["%s【crypto-futures】<b>%s · %s %s</b>" % (ICON.get(sev, "⚠️"), sev, esc(card["asset"]),
                                                     RULE_CN[card["rule"]])]
    if simulated:
        lines.append("🧪 <b>【回放样例】</b>历史数据回放，不是实时行情")
    if card.get("escalated"):
        lines.append("🔥 <b>加剧</b>：同方向幅度扩大到上次的 1.5 倍以上，冷却期内再报")
    lines += ["<b>%s</b>" % esc(_conclusion(card, ctx)), ""]
    lines += [esc(x) for x in _metric_lines(card, ctx)]
    lines.append(esc(_background(ctx)))
    if card.get("also"):
        lines.append("同时出现：%s" % "、".join(RULE_CN[r] for r in card["also"]))
    trend = _trend(ctx)
    reading = (trend + "，" if trend else "") + READING[(card["rule"], card["direction"])]
    tail = ["", esc(reference_line(ref)),
            "口径：%s · 截至 %s" % (SCOPE, common.fmt_local(card["ts"], "%H:%M")), ""]
    foot = "⏱ %s" % common.fmt_local(card["ts"], "%m-%d %H:%M")
    if link:
        foot += ' · 🔗 <a href="%s">查看交易数据</a>' % html.escape(link, quote=True)
    tail += [foot, "🏷 任务：%s" % esc(job or job_label(common.MODULE)), "⚠️ 研究用途，不构成投资建议"]
    # 可见文字超过 MAX_CHARS 时只截「解读」，保证历史参照、口径、页脚与免责声明完整
    budget = MAX_CHARS - visible_len("\n".join(lines + ["解读："] + tail))
    if len(reading) > budget:
        reading = reading[:max(0, budget - 1)] + "…"
    tail[0] = "解读：%s" % esc(reading)
    return truncate_html("\n".join(lines + [""] + tail))


def visible_len(text):
    """去掉 HTML 标签与实体后的可见长度（验收：≤ 600）。"""
    import re
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))
