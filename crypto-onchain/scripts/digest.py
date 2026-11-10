#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""digest.py —— 每日影跑摘要（只汇总；链上大额转账告警是独立事件流）。

用途：M2 影子期每天固定时间（`app.yaml` 的 `digest.time`）推送一条"今天观察到了什么"的
    摘要，方便判断归因是否有信息量；**它不是告警**：不触发 P0/P1、不穿透静默、不发候选明细。

用法：
    python3 scripts/digest.py --dry-run            # 只打印（不写状态、不发送）
    python3 scripts/digest.py --notify             # 发送并记录当日已发
    python3 scripts/digest.py --if-due --notify    # collect.sh 用：到点且今天没发过才发
    python3 scripts/digest.py --force --notify     # 手动补发（忽略"今天已发"）
    python3 scripts/digest.py --json               # 机器可读

数据来源（只读本模块产物 + crypto-price exports）：
    data/state/shadow-latest.json          最新快照（价格背景/资金面/归因）
    data/state/shadow-candidates.jsonl     当日候选
    data/raw/<date>/attributions.jsonl      当日归因记录（run.py 每轮追加）

退出码：0 正常（含"还没到点/今天已发"）；2 配置或凭据问题；3 发送失败。
边界：摘要只回答"卖压类别/资金流向"，不回答"谁在卖"。
"""
import argparse
import datetime
import json
import os
import sys

import common  # isort: skip  # 必须最先导入：common 负责把 libs/crypto_core 加入 sys.path

from crypto_core.notify import check_credentials, esc, job_label, send_message  # noqa: E402

DIGEST_STATE = "state/digest.json"
WF_ALERT_STATE = "state/walletflow-alerts.json"


# ---------------------------------------------------------------- 配置/时间
def _digest_cfg(cfg):
    return ((cfg.get("app") or {}).get("digest") or {})


def _parse_hm(value, default="21:00"):
    try:
        hour, minute = str(value or default).split(":", 1)
        return int(hour), int(minute)
    except (TypeError, ValueError):
        return 21, 0


def is_due(cfg, state, now=None):
    """今天是否该发：到点 + 今天没发过（state.last_sent_date != today）。"""
    cfg_digest = _digest_cfg(cfg)
    if not cfg_digest.get("enabled", True):
        return False, "digest.enabled=false"
    now = now or common.now()
    hour, minute = _parse_hm(cfg_digest.get("time"))
    today = now.strftime("%Y-%m-%d")
    if str((state or {}).get("last_sent_date") or "") == today:
        return False, "今天已发"
    if (now.hour, now.minute) < (hour, minute):
        return False, "未到 %02d:%02d" % (hour, minute)
    return True, "到点待发"


def _calibration(cfg, date_str):
    cfg_digest = _digest_cfg(cfg)
    try:
        start = datetime.date.fromisoformat(str(cfg_digest.get("start_date")))
        today = datetime.date.fromisoformat(date_str)
        total = int(cfg_digest.get("calibration_days") or 14)
        day = (today - start).days + 1
        end = start + datetime.timedelta(days=total - 1)
        return max(1, min(day, total)), total, end.isoformat()
    except (TypeError, ValueError):
        return None, None, None


# ---------------------------------------------------------------- 读取产物
def _read_json(path, default):
    return common.safe_json_load(path, default)


def _read_jsonl(path):
    return common.read_jsonl(path)


def _attribution_rows(paths, date):
    return _read_jsonl(os.path.join(paths["raw"], date, "attributions.jsonl"))


def _walletflow_today(paths, date, cfg=None):
    """当日链上大额转账：计数 + 净流/对倒噪声/单向大额分析（用于每日情报）。"""
    rows = _read_jsonl(os.path.join(paths["raw"], date, "walletflow.jsonl"))
    usd_total = sum(float(r.get("usd") or 0) for r in rows)
    top = sorted(rows, key=lambda r: float(r.get("usd") or 0), reverse=True)[:2]
    directions = {}
    for r in rows:
        d = str(r.get("direction") or "unknown")
        directions[d] = directions.get(d, 0) + 1
    labels = (((cfg or {}).get("app") or {}).get("walletflow") or {}).get("labels") or {}

    def _name(addr):
        return (labels.get(str(addr or "").lower()) or {}).get("name") or ""

    def _usd(rs):
        return sum(float(r.get("usd") or 0) for r in rs)

    usd_in = _usd([r for r in rows if r.get("direction") == "cex_in"])
    usd_out = _usd([r for r in rows if r.get("direction") == "cex_out"])
    label_net = {}
    for r in rows:
        to_name, from_name = _name(r.get("to")), _name(r.get("from"))
        if to_name:
            label_net.setdefault(to_name, [0.0, 0.0])[0] += float(r.get("usd") or 0)
        if from_name:
            label_net.setdefault(from_name, [0.0, 0.0])[1] += float(r.get("usd") or 0)
    net_by_label = sorted(
        ((k, v[0] - v[1]) for k, v in label_net.items()),
        key=lambda kv: -abs(kv[1]),
    )[:3]
    # 双向对倒：同一对地址两个方向都有大额 → 取较小侧求和，视为做市/结算噪声
    pair_usd = {}
    for r in rows:
        key = (str(r.get("from") or "").lower(), str(r.get("to") or "").lower())
        pair_usd[key] = pair_usd.get(key, 0.0) + float(r.get("usd") or 0)
    roundtrip = 0.0
    for (a, b), amount in pair_usd.items():
        rev = pair_usd.get((b, a), 0.0)
        roundtrip += min(amount, rev)
    roundtrip = roundtrip / 2.0
    # 单向大额：方向 unknown 且无反向配对，按金额取前 2
    oneway = []
    for r in sorted(rows, key=lambda r: float(r.get("usd") or 0), reverse=True):
        if r.get("direction") != "unknown":
            continue
        key = (str(r.get("from") or "").lower(), str(r.get("to") or "").lower())
        if pair_usd.get((key[1], key[0])):
            continue
        oneway.append(r)
        if len(oneway) >= 2:
            break
    return {
        "n": len(rows), "usd_total": usd_total, "top": top,
        "cex_in": directions.get("cex_in", 0),
        "cex_out": directions.get("cex_out", 0),
        "cex_internal": directions.get("cex_internal", 0),
        "unknown": directions.get("unknown", 0),
        "usd_in": usd_in, "usd_out": usd_out,
        "net": usd_in - usd_out,
        "net_by_label": net_by_label,
        "roundtrip": roundtrip,
        "roundtrip_ratio": (roundtrip / usd_total) if usd_total else 0.0,
        "oneway": oneway,
    }


def _candidates_today(paths, date, cfg):
    shadow = (cfg.get("app") or {}).get("shadow") or {}
    path = os.path.join(paths["root"], shadow.get("candidates_file") or "state/shadow-candidates.jsonl")
    rows = _read_jsonl(path)
    return [r for r in rows if str(r.get("generated_at") or "").startswith(date)]


def _holdings_today(paths, date):
    """当日已知地址持仓变化事件（run.py 的 holdings.collect 追加；只有超阈值才有）。"""
    return _read_jsonl(os.path.join(paths["raw"], date, "holdings-events.jsonl"))


# ---------------------------------------------------------------- 文本
def _fmt_pct(value, digits=2):
    try:
        return ("%+." + str(digits) + "f%%") % float(value)
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e8:
        return "%.2f 亿美元" % (v / 1e8)
    return "%.0f 万美元" % (v / 1e4)


def _fmt_amount(value):
    """币数量：最多 8 位小数，去尾零，带千分位。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    text = ("%.8f" % number).rstrip("0").rstrip(".")
    integer, _, fraction = text.partition(".")
    if integer.lstrip("-").isdigit():
        integer = "{:,}".format(int(integer))
    return integer + ("." + fraction if fraction else "")


def build_summary(cfg, paths, date=None, now=None):
    """返回 (text_html, data_dict)。只读，不发送。"""
    now = now or common.now()
    date = date or now.strftime("%Y-%m-%d")
    snap = _read_json(os.path.join(paths["state"], "shadow-latest.json"), {}) or {}
    candidates = _candidates_today(paths, date, cfg)
    atts = _attribution_rows(paths, date)
    wf = _walletflow_today(paths, date, cfg)
    wf_alert_state = _read_json(os.path.join(paths["state"], "walletflow-alerts.json"), {}) or {}
    _alerts_cfg = (((cfg.get("app") or {}).get("walletflow") or {}).get("alerts") or {})
    wf_alerts = {
        "messages": wf_alert_state.get("messages") or 0,
        "suppressed": len(wf_alert_state.get("suppressed") or []),
        # only_p0 时 P0 也遵守常规 daily_cap（特殊期才解除）
        "cap": (_alerts_cfg.get("daily_cap") or 10) if _alerts_cfg.get("only_p0")
        else (_alerts_cfg.get("burst_cap") or 30),
    }
    day, total, end = _calibration(cfg, date)

    by_sev = {s: len([c for c in candidates if c.get("severity") == s]) for s in ("P0", "P1")}
    would_alert = len([c for c in candidates if c.get("would_alert")])
    cat_counts = {}
    for row in atts:
        key = row.get("category") or "未知"
        cat_counts[key] = cat_counts.get(key, 0) + 1
    latest_atts = atts[-3:]

    assets = snap.get("assets") or {}
    price_bits = []
    for asset in ("BTC", "ETH", "BNB"):
        row = assets.get(asset) or {}
        if row.get("price") is not None:
            price_bits.append("%s %s" % (asset, _fmt_pct(row.get("change_24h_pct"))))
    cex = snap.get("cex_flows") or {}
    st = snap.get("stablecoins") or {}

    lines = [
        "<b>【crypto-onchain】影子日报 · %s</b>" % esc(date),
        "<i>每日链上情报：净流/噪声过滤/单向大额分析（事件告警默认关闭，避免逐笔打扰）</i>",
        "",
        "· 快照：%s ｜ %s" % (esc(str(snap.get("generated_at") or "暂无")), esc(" / ".join(price_bits) or "行情不可用")),
        "· 今日候选：<b>%d</b> 条（P0 %d / P1 %d；would_alert %d）"
        % (len(candidates), by_sev["P0"], by_sev["P1"], would_alert),
        "· 今日归因记录：<b>%d</b> 条%s"
        % (len(atts), ("（" + esc("、".join("%s×%d" % (k, v) for k, v in cat_counts.items())) + "）") if cat_counts else ""),
    ]
    for row in latest_atts:
        lines.append("  - %s %s（%s）" % (
            esc(str(row.get("asset") or "市场")), esc(str(row.get("category") or "")),
            esc(str(row.get("confidence") or "")),
        ))
    wf_chains = [str(c.get("name")) for c in ((snap.get("walletflow") or {}).get("chains") or []) if c.get("name")]
    wf_label = "ETH 主网 / BSC" if len(wf_chains) > 1 else "ETH 主网"
    wf_line = "· 链上大额转账（%s，USDT/USDC）：<b>%d</b> 笔 ≥ 阈值，合计 %s" % (
        wf_label, wf["n"], _fmt_usd(wf["usd_total"]) if wf["n"] else "—",
    )
    if wf.get("cex_in") or wf.get("cex_out") or wf.get("cex_internal"):
        wf_line += "；入所 <b>%d</b> / 出所 <b>%d</b> / 内部 %d / 未知 %d" % (
            wf.get("cex_in") or 0, wf.get("cex_out") or 0,
            wf.get("cex_internal") or 0, wf.get("unknown") or 0,
        )
    lines.append("· 今日链上告警（仅 P0）：<b>%d</b> 条（上限 %d，特殊期可解除）；P1/抑制 %d 条"
                 % (wf_alerts["messages"], wf_alerts["cap"], wf_alerts["suppressed"]))
    if wf["top"]:
        item = wf["top"][0]
        wf_line += "；最大 %s %s（%s → %s）" % (
            esc(str(item.get("symbol") or "")), _fmt_usd(item.get("usd")),
            esc(str(item.get("from") or "")[:10]), esc(str(item.get("to") or "")[:10]),
        )
    lines.append(wf_line)
    if wf.get("usd_total"):
        net_line = "· 链上净流：入所 %s / 出所 %s / 净 %s" % (
            _fmt_usd(wf.get("usd_in") or 0), _fmt_usd(wf.get("usd_out") or 0),
            ("+" if (wf.get("net") or 0) >= 0 else "") + _fmt_usd(abs(wf.get("net") or 0)),
        )
        if wf.get("net_by_label"):
            net_line += "（%s）" % "、".join(
                "%s %+.0fM" % (k, v / 1e6) for k, v in wf["net_by_label"]
            )
        lines.append(net_line)
        if wf.get("roundtrip"):
            lines.append("· 噪声过滤：同对地址双向对倒 ~%s（占 %.0f%%），单笔大额多为做市/结算，不代表方向" % (
                _fmt_usd(wf["roundtrip"]), (wf.get("roundtrip_ratio") or 0) * 100))
        if wf.get("oneway"):
            parts = []
            for r in wf["oneway"]:
                tx = str(r.get("tx") or "")
                url = ("https://bscscan.com/tx/" if r.get("chain") == "bsc" else "https://etherscan.io/tx/") + tx
                parts.append("%s %s→%s（%s）" % (
                    _fmt_usd(r.get("usd")), str(r.get("from") or "")[:8],
                    str(r.get("to") or "")[:8], url))
            lines.append("· 单向大额（方向不明，值得留意）：%s" % "；".join(parts))
    hold = snap.get("holdings") or {}
    hold_events = _holdings_today(paths, date)
    if hold.get("enabled"):
        hold_entries = hold.get("entries") or []
        if hold.get("usd_total") is not None:
            hold_usd = _fmt_usd(hold.get("usd_total"))
        else:
            hold_usd = "价格不可得（只报数量）"
        lines.append("· 已知地址持仓（人工配置 %d 个）：合计 %s；今日变化事件 <b>%d</b> 条%s" % (
            len(hold_entries), hold_usd, len(hold_events),
            "（缓存）" if hold.get("cached") else "",
        ))
        for row in hold_entries[:3]:
            change = row.get("change_pct")
            lines.append("  - %s %s %s（%s）" % (
                esc(str(row.get("label") or "")[:24]), esc(str(row.get("symbol") or "")),
                _fmt_amount(row.get("balance")),
                ("较上次 %+.2f%%" % change) if change is not None else "无上次基线",
            ))
        if hold.get("n_errors"):
            lines.append("  - ⚠ 取数失败 %d 个地址（保留上次余额，详见快照）" % hold["n_errors"])
    lines += [
        "· 资金面：CEX 24h 净流 %s bps TVL（%s）；稳定币 24h 净增发 %s（负=净赎回）"
        % (_fmt_pct(cex.get("net_24h_bps"), 1).replace("%", ""), _fmt_usd(cex.get("net_24h_usd")),
           _fmt_usd(st.get("net_issue_24h_usd"))),
        "· 校准进度：Day %s/%s（至 %s；到期回算精确率，达标才开告警）"
        % (day if day else "?", total if total else "?", esc(str(end or "?"))),
        "",
        "口径：CEX 净流为全币种聚合 USD；清算仅 OKX；只回答卖压类别，不回答“谁在卖”。",
    ]
    text = "\n".join(lines)
    data = {
        "date": date,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "snapshot_at": snap.get("generated_at"),
        "candidates": len(candidates),
        "by_severity": by_sev,
        "would_alert": would_alert,
        "attributions": len(atts),
        "attribution_categories": cat_counts,
        "latest_attributions": latest_atts,
        "walletflow": wf,
        "holdings": {
            "n": len((snap.get("holdings") or {}).get("entries") or []),
            "usd_total": (snap.get("holdings") or {}).get("usd_total"),
            "cached": (snap.get("holdings") or {}).get("cached"),
            "events": len(hold_events),
            "events_summary": hold_events,
        },
        "cex_net_24h_bps": cex.get("net_24h_bps"),
        "stablecoin_net_issue_24h_usd": st.get("net_issue_24h_usd"),
        "calibration_day": day,
        "calibration_total": total,
        "calibration_end": end,
    }
    return text, data


# ---------------------------------------------------------------- 状态/发送
def _load_state(paths):
    return _read_json(os.path.join(paths["root"], DIGEST_STATE), {}) or {}


def _save_state(paths, state):
    common.write_json(os.path.join(paths["root"], DIGEST_STATE), state)


def send_digest(cfg, paths, text, dry_run=False, force=False, now=None):
    """发送并记录状态；dry-run 只打印。返回 (ok, error|None, skipped_reason|None)。"""
    now = now or common.now()
    state = _load_state(paths)
    if not force:
        due, reason = is_due(cfg, state, now=now)
        if not due:
            return True, None, reason
    if dry_run:
        print(text)
        return True, None, None
    env = common.load_bot_env()
    token, chat, problems = check_credentials(env)
    if problems:
        return False, "凭据不完整：%s" % "；".join(problems), None
    full = text.rstrip() + "\n\n🏷 任务：%s" % job_label("crypto-onchain")
    ok, err = send_message(token, chat, full, disable_preview=True)
    if not ok:
        return False, err, None
    state["last_sent_date"] = now.strftime("%Y-%m-%d")
    state["last_sent_at"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    _save_state(paths, state)
    common.log_line("digest: sent %s" % state["last_sent_date"], paths, filename="onchain.log")
    return True, None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-onchain 每日影跑摘要（只汇总，不告警）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写状态、不发送")
    parser.add_argument("--notify", action="store_true", help="发送到 Telegram")
    parser.add_argument("--if-due", action="store_true", help="到点且今天没发过才发（collect.sh 用）")
    parser.add_argument("--force", action="store_true", help="忽略'今天已发'，手动补发")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--date", default=None, help="指定统计日期（YYYY-MM-DD，回放用）")
    parser.add_argument("--data-dir", default=None, help="data/ 重定向（测试/回放隔离）")
    args = parser.parse_args(argv)

    cfg = common.load_all_configs()
    for name in common.CONFIG_FILES:
        problem = common.schema_problem(cfg[name[:-5]], 1)
        if problem:
            print("❌ 配置错误：%s %s" % (name, problem), file=sys.stderr)
            return 2
    common.apply_timezone((cfg["app"] or {}).get("timezone"))
    paths = common.data_paths(args.data_dir)
    text, data = build_summary(cfg, paths, date=args.date)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        if args.notify or args.if_due:
            ok, err, skipped = send_digest(
                cfg, paths, text, dry_run=args.dry_run,
                force=args.force or (args.notify and not args.if_due),
            )
            if not ok:
                print("❌ 发送失败：%s" % err, file=sys.stderr)
                return 3
            if skipped:
                print("跳过：%s" % skipped, file=sys.stderr)
        return 0
    if not (args.notify or args.if_due):
        print(text)
        return 0
    # --if-due = collect.sh 的每日入口（到点且今天没发才发）；--notify = 手动发送（忽略到点）
    ok, err, skipped = send_digest(
        cfg, paths, text, dry_run=args.dry_run,
        force=args.force or (args.notify and not args.if_due),
    )
    if skipped:
        print("跳过：%s" % skipped)
        return 0
    if not ok:
        print("❌ 发送失败：%s" % err, file=sys.stderr)
        return 3
    if not args.dry_run:
        print("已发送影跑摘要（%s）" % data["date"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
