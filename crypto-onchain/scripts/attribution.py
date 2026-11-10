#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attribution.py —— 归因引擎（M2）：把单信号候选组合成"卖压类别"结论。

四个类别（对应 docs/crypto-onchain/requirement.md §4.3）：
  1. long_liquidation  多头被强平（被动卖盘）
  2. short_add / active_dump  空头加仓 / 主动砸盘
  3. capital_exit      增量资金撤离（卖压背景）
  4. deleverage        去杠杆（反证：不是新增抛售）

输出固定三段：evidence（证据）、counter_evidence（反证条件）、unknown（不可知说明）。
**永不输出身份结论**：类别只回答"卖的是哪类仓位/哪类资金"，不回答"谁"。

输入都是纯数据（不做网络/写盘），阈值全部来自 rules.yaml（attribution 段），
函数内的默认值只作键缺失兜底，与 YAML 初值一致。
"""
import math

# 类别常量（导出层/测试用）
LONG_LIQ = "long_liquidation"
SHORT_ADD = "short_add"
CAPITAL_EXIT = "capital_exit"
DELEVERAGE = "deleverage"

CATEGORY_CN = {
    LONG_LIQ: "多头被强平（被动卖盘）",
    SHORT_ADD: "空头加仓 / 主动砸盘",
    CAPITAL_EXIT: "增量资金撤离（卖压背景）",
    DELEVERAGE: "去杠杆（非新增抛售）",
}

CATEGORY_BY = {
    LONG_LIQ: "清算数据 + 价格方向",
    SHORT_ADD: "逐笔主动买卖 + OI/资金费率",
    CAPITAL_EXIT: "CEX 储备净流 + 稳定币净增发",
    DELEVERAGE: "OI 下降 + 资金流/清算未恶化",
}

UNKNOWN_DEFAULT = "无法判断卖方身份（CEX 匿名撮合、链上地址假名）；本结论只是概率归因"


def _f(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _pct(value, digits=2):
    v = _f(value)
    return "—" if v is None else ("%+." + str(digits) + "f%%") % v


def _usd(value, digits=1):
    v = _f(value)
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return "%.2f 亿美元" % (v / 1e8)
    return "%.1f 万美元" % (v / 1e4)


def _base(asset, category, confidence, evidence, counter, extra_unknown=None):
    unknown = UNKNOWN_DEFAULT
    if extra_unknown:
        unknown = unknown + "；" + extra_unknown
    return {
        "asset": asset,
        "category": CATEGORY_CN[category],
        "category_code": category,
        "confidence": confidence,
        "evidence": [e for e in evidence if e],
        "counter_evidence": [c for c in counter if c],
        "unknown": unknown,
        "basis": CATEGORY_BY[category],
    }


def _liq_evidence(liq):
    total = _f(liq.get("total_usd"))
    long_ratio = _f(liq.get("long_ratio"))
    coverage = _f(liq.get("coverage_minutes"))
    parts = []
    if total is not None:
        parts.append("OKX 15m 清算 %s（%s）" % (_usd(total), "多头占比 %d%%" % round(long_ratio * 100) if long_ratio is not None else "多空比未知"))
    if coverage is not None:
        parts.append("清算样本覆盖 %.0f 分钟" % coverage)
    return "；".join(parts)


def attribute_asset(asset, row, rules, cex_flows, stablecoins, oi_change_pct=None):
    """单资产归因。row 为快照里的资产结构（price/change/direction/taker_5m/liq_15m/funding_rate）。

    只在 24h 方向为 down（或 15m 主动卖占比显著）时输出卖压归因；否则返回 None。
    """
    conf_rules = (rules or {}).get("attribution") or {}
    if conf_rules.get("enabled", True) is False:
        return None
    direction = str(row.get("direction") or "unknown")
    change = _f(row.get("change_24h_pct"))
    taker = row.get("taker_5m") or {}
    liq = row.get("liq_15m") or {}
    funding = _f(row.get("funding_rate"))
    oi = _f(oi_change_pct)
    taker_ratio = _f(taker.get("sell_ratio"))
    taker_vol = _f(taker.get("quote_volume_usd")) or 0.0
    large_sells = int(taker.get("large_sell_count") or 0)
    liq_total = _f(liq.get("total_usd")) or 0.0
    long_ratio = _f(liq.get("long_ratio"))

    taker_rules = (rules or {}).get("taker") or {}
    liq_rules = (rules or {}).get("liquidations") or {}
    cex_rules = (rules or {}).get("cex_flow") or {}
    st_rules = (rules or {}).get("stablecoin") or {}

    oi_sig = _f(conf_rules.get("oi_significant_pct")) or 2.0   # 15m OI 变化 ≥2% 视为显著
    p1_ratio = _f(taker_rules.get("p1_ratio")) or 0.58
    liq_p1 = ((liq_rules.get("p1_usd") or {}).get(asset)) or 0
    long_bias = _f(liq_rules.get("long_bias_ratio")) or 0.6
    cex_p1 = _f(cex_rules.get("p1_net_24h_bps_tvl")) or 50
    st_p1 = _f(st_rules.get("p1_net_issue_24h_usd")) or -500000000
    min_vol = ((taker_rules.get("min_window_quote_usd") or {}) or {}).get(asset) or 0
    if isinstance(min_vol, (int, float)) and taker_vol < min_vol:
        taker_ratio = None  # 样本不足时不参与归因
    price_down = direction == "down" or (change is not None and change <= -0.5)
    if not price_down:
        return None

    price_ev = "价格 24h %s" % _pct(change) if change is not None else "价格方向向下"
    taker_ev = None
    if taker_ratio is not None:
        taker_ev = "5m 主动卖占比 %.1f%%（大额卖单 %d 笔，成交额 %s）" % (
            taker_ratio * 100, large_sells, _usd(taker_vol))
    oi_ev = "15m OI 变化 %s" % _pct(oi) if oi is not None else "OI 变化未知（快照未含/历史不足）"
    funding_ev = "资金费率 %+.4f%%" % (funding * 100) if funding is not None else None
    evidence_common = [price_ev, funding_ev]

    # 1) 多头被强平：清算额达 P1 且多头占比高
    if liq_total >= liq_p1 and liq_p1 > 0 and long_ratio is not None and long_ratio >= long_bias:
        conf = "medium"
        if oi is not None and oi <= -oi_sig:
            conf = "high"
        elif oi is not None and oi > 0:
            conf = "low"
        ev = evidence_common + [_liq_evidence(liq), taker_ev, oi_ev]
        counter = [
            "若 OI 未下降，则更可能是新开空/主动抛售，而不是强平",
            "清算数据仅 OKX 口径，不代表全市场",
        ]
        return _base(asset, LONG_LIQ, conf, ev, counter,
                     extra_unknown="被强平的具体账户不可见")

    # 2) 空头加仓 / 主动砸盘：主动卖占比高 且 OI 上升（或未知）
    if taker_ratio is not None and taker_ratio >= p1_ratio and (oi is None or oi >= -oi_sig):
        conf = "medium"
        if oi is not None and oi >= oi_sig:
            conf = "high" if (funding is not None and funding <= 0) else "medium"
        ev = evidence_common + [taker_ev, oi_ev]
        counter = [
            "大额卖单可能是做市商对冲/对敲，未必是方向性抛售",
            "若 OI 明显下降，应改判为多头强平/去杠杆",
        ]
        return _base(asset, SHORT_ADD, conf, ev, counter,
                     extra_unknown="主动卖单属于新开空、平多还是搬砖不可区分")

    # 3) 增量资金撤离：CEX 储备净流出 + 稳定币净赎回（市场级背景，资产级沿用）
    cex_bps = _f((cex_flows or {}).get("net_24h_bps"))
    st_net = _f((stablecoins or {}).get("net_issue_24h_usd"))
    if cex_bps is not None and st_net is not None and cex_bps <= -cex_p1 and st_net <= st_p1:
        ev = evidence_common + [
            "关注交易所储备 24h 净流 %+.1fbps TVL（%s）" % (cex_bps, _usd((cex_flows or {}).get("net_24h_usd"))),
            "稳定币 24h 净赎回 %s" % _usd(st_net),
            oi_ev,
        ]
        counter = [
            "提币到自托管 ≠ 卖出；CEX 净流是全币种聚合，不能归到单一币种",
            "稳定币换链 mint/burn 会造成净增发噪音",
        ]
        return _base(asset, CAPITAL_EXIT, "medium", ev, counter,
                     extra_unknown="资金流出后是否卖出不可见")

    # 4) 去杠杆（反证）：OI 显著下降，且没有强平/主动卖/资金撤离的证据
    if oi is not None and oi <= -oi_sig and (taker_ratio is None or taker_ratio < p1_ratio):
        cex_ok = cex_bps is None or cex_bps >= -cex_p1
        if cex_ok:
            ev = evidence_common + [oi_ev, taker_ev]
            counter = [
                "若随后放量下跌且 OI 回升，则从去杠杆切换为新增抛售",
                "OI 数据来自单所（Binance USD-M），跨所杠杆变化不可见",
            ]
            return _base(asset, DELEVERAGE, "medium", ev, counter,
                         extra_unknown="杠杆资金来自哪类账户不可见")
    return None


def attribute_market(cex_flows, stablecoins, btc_direction, rules=None):
    """市场级归因：只看"资金撤离"这一类（CEX 净流 + 稳定币净赎回）。"""
    conf_rules = (rules or {}).get("attribution") or {}
    if conf_rules.get("enabled", True) is False:
        return None
    if str(btc_direction) != "down":
        return None
    cex_bps = _f((cex_flows or {}).get("net_24h_bps"))
    st_net = _f((stablecoins or {}).get("net_issue_24h_usd"))
    if cex_bps is None or st_net is None:
        return None
    cex_p1 = _f(((rules or {}).get("cex_flow") or {}).get("p1_net_24h_bps_tvl")) or 50.0
    st_p1 = _f(((rules or {}).get("stablecoin") or {}).get("p1_net_issue_24h_usd")) or -500000000.0
    if cex_bps <= -cex_p1 and st_net <= st_p1:
        ev = [
            "交易所储备 24h 净流 %+.1fbps TVL（%s）" % (cex_bps, _usd((cex_flows or {}).get("net_24h_usd"))),
            "稳定币 24h 净赎回 %s" % _usd(st_net),
            "BTC 24h 方向 down",
        ]
        counter = [
            "CEX 净流是全币种聚合 USD，不能断言是 BTC/ETH 被转出",
            "稳定币净赎回可能是换链或赎回套利，不等于离场",
        ]
        return {
            "asset": None,
            "category": CATEGORY_CN[CAPITAL_EXIT],
            "category_code": CAPITAL_EXIT,
            "confidence": "medium" if abs(cex_bps) < 150 else "high",
            "evidence": ev,
            "counter_evidence": counter,
            "unknown": UNKNOWN_DEFAULT + "；市场级只有总量，没有币种/主体拆分",
            "basis": CATEGORY_BY[CAPITAL_EXIT],
        }
    return None


def build_attributions(assets, cex_flows, stablecoins, rules, directions, oi_changes=None):
    """返回 {"assets": {asset: attribution|None}, "market": attribution|None}。"""
    oi_changes = oi_changes or {}
    out_assets = {}
    for asset, row in (assets or {}).items():
        out_assets[asset] = attribute_asset(
            asset, row, rules, cex_flows, stablecoins, oi_change_pct=oi_changes.get(asset),
        )
    market = attribute_market(cex_flows, stablecoins, (directions or {}).get("BTC"), rules=rules)
    return {"assets": out_assets, "market": market}
