#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cards.py —— 价格告警的 Telegram HTML 卡片。

与 crypto-news 的新闻卡片分开：价格卡片强调「现价 / 各窗口涨跌 / 多源核验 / 触发规则」，
不展示新闻评分明细。所有文本走 HTML 转义，超长截断。
"""
import html
import os

from crypto_core import timeutil
from crypto_core.notify.templates import MAX_LEN, esc

ICON = {"P0": "🚨", "P1": "⚠️", "P2": "ℹ️"}
SEVERITY_LABEL = {"P0": "P0", "P1": "P1", "P2": "P2"}
RULE_CN = {
    "fast_move": "急涨急跌",
    "day_move": "24h 大幅波动",
    "new_extreme": "24h 新高/新低",
    "volume_spike": "成交量放大",
    "funding": "资金费率极端",
    "open_interest": "持仓量异动",
    "level_break": "关键位突破",
    "data_quality": "数据质量异常",
}


def fmt_price(value, digits=2):
    """价格格式化：75,412.00 / 2,251.33；None -> —。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1000:
        return "{:,.{d}f}".format(v, d=digits)
    return "{:,.{d}f}".format(v, d=digits if abs(v) >= 1 else 4)


def fmt_amount(value):
    """带符号金额，如 -120.33 / +76.90。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if v > 0 else "-" if v < 0 else ""
    return sign + fmt_price(abs(v))


def fmt_price_unit(value):
    return fmt_price(value) + " USDT"


def fmt_pct(value, digits=2, signed=True):
    """涨跌幅格式化：-2.10% / +1.23%；None -> —。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return ("{:+.{d}f}%" if signed else "{:.{d}f}%").format(v, d=digits)


def fmt_bps(value, digits=1):
    try:
        return "{:.{d}f}bps".format(float(value), d=digits)
    except (TypeError, ValueError):
        return "—"


def direction_cn(direction):
    return {"up": "上涨", "down": "下跌", "flat": "异动"}.get(direction or "", "异动")


SOURCE_LABEL = {"binance": "Binance", "okx": "OKX", "coinbase": "Coinbase", "cmc": "CMC"}


def format_source_status(source_health, primary_sources, spread_bps=None, max_spread_bps=None,
                        excluded_sources=None, n_sources=None):
    """多源核验摘要：3/3 binance/okx/coinbase（数据正常） / 2/3 ...（coinbase 获取异常）。"""
    health = source_health if isinstance(source_health, dict) else {}
    primary = list(primary_sources or health.keys())
    if not primary:
        return ""
    ok = [name for name in primary if (health.get(name) or {}).get("ok") is True]
    bad = [name for name in primary if name not in ok]
    text = "%d/%d %s" % (len(ok), len(primary), "/".join(primary))
    if bad:
        text += "（%s 获取异常）" % "、".join(bad)
    elif excluded_sources:
        text += "（%s 偏离已剔除，%s 源有效）" % (
            "、".join(excluded_sources), n_sources if n_sources is not None else len(primary) - len(excluded_sources))
    elif spread_bps is not None and max_spread_bps is not None and spread_bps > max_spread_bps:
        text += "（已获取，但价差 %s 偏大）" % fmt_bps(spread_bps)
    else:
        text += "（数据正常）"
    return text


def source_status_text(alert):
    metrics = alert.get("metrics") or {}
    max_spread = metrics.get("max_spread_bps")
    return format_source_status(alert.get("source_health"), alert.get("primary_sources"),
                                metrics.get("spread_bps"),
                                max_spread if max_spread is not None else 50,
                                metrics.get("excluded_sources"), metrics.get("n_sources"))


def _sources_line(evidence):
    """把 evidence（[{"source": "binance", "price": ...}]）渲染成「Binance · OKX · Coinbase」。"""
    names = []
    for item in evidence or []:
        src = (item.get("source") or "").strip()
        label = SOURCE_LABEL.get(src.lower(), src)
        if label and label not in names:
            names.append(label)
    return " · ".join(names)


def _truncate(text):
    if len(text) <= MAX_LEN:
        return text
    cut = text[: MAX_LEN - 1]
    # 不切断 HTML 标签
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    # 不切断 HTML 实体（如 &amp; / &#39;）
    amp = cut.rfind("&")
    semi = cut.rfind(";")
    if amp > semi and MAX_LEN - len(cut) < 10:
        cut = cut[:amp]
    return cut + "…"


def format_price_alert(alert):
    """渲染一条价格告警（alert 为 delivery.event_to_alert() 产出的 dict）。"""
    sev = alert.get("severity") or "P1"
    assets = alert.get("assets") or []
    asset = assets[0] if assets else ""
    icon = ICON.get(sev, "⚠️")
    title = alert.get("title") or ""

    lines = ["%s【crypto-price】<b>%s · %s 价格异动</b>" % (
        icon, SEVERITY_LABEL.get(sev, sev), esc(asset))]
    if alert.get("simulated"):
        lines.append("🧪 <b>【模拟测试】</b> 离线回放数据，不是真实行情（仅验证告警链路）")
    if alert.get("escalated"):
        lines.append("🔥 <b>波动加剧</b>（同方向幅度继续扩大，已绕过冷却再报）")
    lines.append("<b>%s</b>" % esc(title))

    metrics = alert.get("metrics") or {}
    price_value = metrics.get("price")
    price_label = "现价"
    if price_value is None and metrics.get("mark_price") is not None:
        price_value = metrics.get("mark_price")
        price_label = "标记价"
    if price_value is not None:
        lines.append("<b>%s</b>：%s" % (price_label, fmt_price_unit(price_value)))

    rule = alert.get("rule")
    change_pct = metrics.get("change_pct")
    if change_pct is None:
        change_pct = metrics.get("price_change_pct")
    if change_pct is not None and price_value is not None and rule in (
            "fast_move", "day_move", "volume_spike", "open_interest"):
        amount = float(price_value) * float(change_pct) / 100.0
        lines.append("<b>变动</b>：%s（%s USDT）" % (fmt_pct(change_pct), fmt_amount(amount)))

    detail = alert.get("detail") or []
    if detail:
        lines.append("")
        for d in detail[:6]:
            lines.append("• %s" % esc(d))

    summary = alert.get("summary") or {}
    if summary.get("why"):
        lines.append("")
        lines.append("<b>影响</b>：%s" % esc(summary["why"]))
    if summary.get("action"):
        action = str(summary["action"])
        if "不构成投资建议" not in action:
            action += "（不构成投资建议）"
        lines.append("<b>动作参考</b>：%s" % esc(action))

    spread = metrics.get("spread_bps")
    status = source_status_text(alert)
    src = _sources_line(alert.get("evidence") or [])
    if status:
        extra = []
        if spread is not None:
            extra.append("价差 %s" % fmt_bps(spread))
        line = status + (" · " + " · ".join(extra) if extra else "")
        lines.append("<b>多源核验</b>：%s" % esc(line))
    elif src or spread is not None:
        bits = []
        if src:
            bits.append("%s 中位数" % src)
        if spread is not None:
            bits.append("三源价差 %s" % fmt_bps(spread))
        lines.append("<b>核验</b>：%s" % esc(" · ".join(bits)))

    rule = alert.get("rule")
    if rule:
        rule_text = RULE_CN.get(rule)
        lines.append("<b>触发</b>：%s" % esc(
            "%s（%s）" % (rule_text, rule) if rule_text else rule))

    meta = []
    ts = alert.get("ts")
    if ts:
        meta.append(timeutil.fmt_local(ts, "%m-%d %H:%M"))
    if alert.get("poll_seconds"):
        meta.append("%ss 轮询" % alert["poll_seconds"])
    if meta:
        lines.append("⏱ %s" % esc(" · ".join(meta)))

    urls = alert.get("urls") or []
    if urls:
        lines.append('🔗 <a href="%s">查看行情</a>' % html.escape(str(urls[0]), quote=True))
    job = (alert.get("job") or os.environ.get("CRYPTO_JOB_LABEL")
           or os.environ.get("XPC_SERVICE_NAME") or "")
    if not job.startswith("com.crypto-research"):
        job = "crypto-price（手动运行）"
    if alert.get("simulated"):
        job += "（模拟测试）"
    lines.append("🏷 任务：%s" % esc(job))

    return _truncate("\n".join(lines))
