#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report.py —— 每日行情/告警早报。

- 每天在 app.report.time（默认 08:00）后的第一轮生成一次：data/reports/<date>.md
- 可选推送一条 Telegram 摘要（app.report.send）；夜间被压制的 P1 会合并进早报
- 报告只读当前快照 + 最近 24h 审计，不改任何行情状态
"""
import datetime
import os

import cards
import delivery as delivery_mod

import common
from crypto_core import timeutil

REPORT_END_HOUR = 22  # 22:00 后进入静默时段，不再生成/推送日报
RETRY_THROTTLE_SEC = 600  # 早报发送失败后 10 分钟内不重试，避免每 60s 刷屏


def _as_dt(now=None):
    if now is None:
        return timeutil.now()
    if isinstance(now, datetime.datetime):
        return now
    return datetime.datetime.fromtimestamp(float(now))


def _parse_hhmm(text, default=(8, 0)):
    try:
        h, m = str(text).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return default


def _state_path(state_dir):
    return os.path.join(str(state_dir), "report.json")


def load_state(state_dir):
    raw = common.safe_json_load(_state_path(state_dir), {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "last_generated_date": raw.get("last_generated_date"),
        "last_sent_date": raw.get("last_sent_date"),
        "last_ts": raw.get("last_ts"),
        "last_attempt_ts": raw.get("last_attempt_ts") or 0,
    }


def save_state(state_dir, state):
    common.safe_json_dump(_state_path(state_dir), state)
    return state


def is_due(now, app_cfg, state):
    """是否到了生成日报的窗口：>= 配置时间、< 22:00、今天还没生成、不在失败重试冷却期。"""
    now_dt = _as_dt(now)
    report_cfg = (app_cfg or {}).get("report") or {}
    if not report_cfg.get("enabled"):
        return False
    if now_dt.hour >= REPORT_END_HOUR:
        return False
    hour, minute = _parse_hhmm(report_cfg.get("time", "08:00"))
    if (now_dt.hour, now_dt.minute) < (hour, minute):
        return False
    if state.get("last_generated_date") == timeutil.today_str(now_dt):
        return False
    last_attempt = state.get("last_attempt_ts") or 0
    try:
        if last_attempt and now_dt.timestamp() - float(last_attempt) < RETRY_THROTTLE_SEC:
            return False
    except (TypeError, ValueError):
        pass
    return True


# ---------------------------------------------------------------- 数据读取
def _recent_alerts(state_dir, now_dt, hours=24):
    """从昨天/今天的 alerts-<date>.json 里取最近 24h 的判定记录。"""
    cutoff = now_dt - datetime.timedelta(hours=hours)
    out = []
    for date in (timeutil.today_str(now_dt - datetime.timedelta(days=1)), timeutil.today_str(now_dt)):
        rows = common.safe_json_load(os.path.join(str(state_dir), "alerts-%s.json" % date), [])
        if not isinstance(rows, list):
            continue
        for a in rows:
            if not isinstance(a, dict):
                continue
            ts = a.get("ts")
            try:
                dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
            out.append(a)
    return out


def _market_rows(snapshot, watchlist):
    assets_cfg = (watchlist or {}).get("assets") or {}
    quotes = (snapshot or {}).get("quotes") or {}
    stats = (snapshot or {}).get("stats") or {}
    rows = []
    for symbol in assets_cfg:
        q = quotes.get(symbol) or {}
        st = stats.get(symbol) or {}
        price = q.get("price") if q.get("price") is not None else st.get("last")
        high = st.get("high")
        low = st.get("low")
        chg = st.get("change_pct")
        if chg is None and price and st.get("open"):
            try:
                chg = (float(price) / float(st["open"]) - 1) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                chg = None
        if price and high and low:
            try:
                span = float(high) - float(low)
                pos = (float(price) - float(low)) / span if span else None
            except (TypeError, ValueError, ZeroDivisionError):
                pos = None
        else:
            pos = None
        rows.append({
            "symbol": symbol, "price": price, "change_pct": chg,
            "high": high, "low": low, "pos": pos,
            "n_sources": q.get("n_sources"), "spread_bps": q.get("spread_bps"),
        })
    return rows


def _format_pos(pos):
    if pos is None:
        return "—"
    return "区间 %.0f%%" % (pos * 100)


def build_markdown(date, snapshot, state_dir, watchlist, now=None):
    now_dt = _as_dt(now)
    rows = _market_rows(snapshot, watchlist)
    alerts = _recent_alerts(state_dir, now_dt)
    delivered = [a for a in alerts if (a.get("delivery") or {}).get("decision") in ("sent", "held", "failed")]
    held = delivery_mod.held_events(state_dir, now_dt)
    counts = {"P0": 0, "P1": 0}
    for a in delivered:
        sev = a.get("severity")
        if sev in counts:
            counts[sev] += 1

    lines = [
        "# crypto-price 日报 %s" % date,
        "",
        "> 生成时间：%s｜数据源：Binance / OKX / Coinbase（免 key）｜轮询 60s"
        % timeutil.fmt_local(now_dt),
        "",
        "## 行情（过去 24h）",
        "",
        "| 资产 | 现价 | 24h | 24h 高 | 24h 低 | 位置 | 源/价差 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["symbol"], cards.fmt_price(r["price"]), cards.fmt_pct(r["change_pct"]),
            cards.fmt_price(r["high"]), cards.fmt_price(r["low"]), _format_pos(r["pos"]),
            "%s 源 / %s" % (r["n_sources"] if r["n_sources"] is not None else "—",
                            cards.fmt_bps(r["spread_bps"])),
        ))
    lines += [
        "",
        "## 告警（最近 24h）",
        "",
        "- P0：%d 条；P1：%d 条；P2/归档：%d 条" % (
            counts["P0"], counts["P1"],
            max(0, len(alerts) - len(delivered)),
        ),
        "- 当前处于夜间压制、待早报汇总：%d 条" % len(held),
    ]
    if delivered:
        by_rule = {}
        for a in delivered:
            rule = a.get("rule") or "unknown"
            by_rule[rule] = by_rule.get(rule, 0) + 1
        lines.append("- 按规则：" + "、".join("%s×%d" % (k, v) for k, v in sorted(by_rule.items())))
    if held:
        lines += ["", "### 夜间压制明细", ""]
        for h in held[:20]:
            lines.append("- %s %s %s（%s）" % (
                h.get("ts", "")[5:16].replace("T", " "), h.get("severity", ""),
                h.get("title", ""), h.get("reason", ""),
            ))
    source_status = cards.format_source_status(
        (snapshot or {}).get("source_health"), (snapshot or {}).get("primary_sources"))
    errors = (snapshot or {}).get("errors") or []
    lines += [
        "",
        "## 数据源健康",
        "",
        "- 多源核验：%s" % (source_status or "—"),
        "- 本轮成功 %s / %s 个源；失败 %d 次" % (
            (snapshot or {}).get("sources_ok", "—"),
            (snapshot or {}).get("sources_total", "—"), len(errors),
        ),
    ]
    for e in errors[:8]:
        lines.append("  - %s/%s：%s" % (e.get("source"), e.get("asset"), e.get("error")))
    lines += ["", "> 告警阈值见 `config/rules.yaml`；本报告只做记录，不构成投资建议。", ""]
    return "\n".join(lines)


def build_telegram(date, snapshot, state_dir, watchlist, now=None):
    now_dt = _as_dt(now)
    rows = _market_rows(snapshot, watchlist)
    alerts = _recent_alerts(state_dir, now_dt)
    delivered = [a for a in alerts if (a.get("delivery") or {}).get("decision") in ("sent", "held", "failed")]
    held = delivery_mod.held_events(state_dir, now_dt)
    counts = {"P0": 0, "P1": 0}
    for a in delivered:
        if a.get("severity") in counts:
            counts[a["severity"]] += 1

    parts = ["📊【crypto-price】<b>早报 %s</b>" % date[5:]]
    for r in rows:
        line = "<b>%s</b> %s（%s）" % (
            cards.esc(r["symbol"]), cards.fmt_price(r["price"]), cards.fmt_pct(r["change_pct"]))
        if r["high"] is not None and r["low"] is not None:
            line += "｜24h %s – %s" % (cards.fmt_price(r["low"]), cards.fmt_price(r["high"]))
        parts.append(line)
    source_status = cards.format_source_status(
        (snapshot or {}).get("source_health"), (snapshot or {}).get("primary_sources"))
    if source_status:
        parts.append("<b>多源核验</b>：%s" % cards.esc(source_status))
    parts.append("")
    parts.append("<b>告警</b>：P0 %d · P1 %d · 夜间压制 %d" % (counts["P0"], counts["P1"], len(held)))
    if held:
        parts.append("<b>夜间压制</b>：")
        for h in held[:5]:
            parts.append("· %s %s" % (h.get("ts", "")[5:16].replace("T", " "), cards.esc(h.get("title", ""))))
        if len(held) > 5:
            parts.append("· …另有 %d 条" % (len(held) - 5))
    errs = (snapshot or {}).get("errors") or []
    if errs:
        parts.append("⚠️ 数据源失败 %d 次（详见日报）" % len(errs))
    parts.append("")
    parts.append("详情：crypto-price/data/reports/%s.md" % date)
    parts.append("🏷 任务：%s" % common.job_name("crypto-price"))
    return "\n".join(parts)


# ---------------------------------------------------------------- 主入口
def maybe_run(state_dir, snapshot, watchlist, app_cfg, env=None, now=None, dry_run=False,
              send_fn=None, force=False):
    """若到点且今天未生成：写日报（+可选推送）。返回 {generated, sent, path, reason}。

    force=True 供手工 --report 使用：跳过时间窗口与"今日已生成"判断。
    """
    now_dt = _as_dt(now)
    state = load_state(state_dir)
    if not force and not is_due(now_dt, app_cfg, state):
        return {"generated": False, "sent": False, "reason": "未到日报窗口或今日已生成"}

    date = timeutil.today_str(now_dt)
    md = build_markdown(date, snapshot, state_dir, watchlist, now_dt)
    report_dir = os.path.join(str(state_dir).rsplit(os.sep, 1)[0], "reports")
    path = os.path.join(report_dir, "%s.md" % date)
    sent = False
    errors = []
    report_cfg = (app_cfg or {}).get("report") or {}
    html = build_telegram(date, snapshot, state_dir, watchlist, now_dt)

    if dry_run:
        return {"generated": False, "sent": False, "reason": "dry-run", "preview": md, "telegram": html}

    os.makedirs(report_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    state["last_attempt_ts"] = now_dt.timestamp()
    state["last_ts"] = timeutil.ts_iso(now_dt)
    generated = True
    sent_required = report_cfg.get("send", True)

    if sent_required:
        env = env or common.load_env()
        from crypto_core.notify import check_credentials, send_message
        token, chat_id, problems = check_credentials(env)
        if problems:
            errors.append("Telegram 凭据不完整：%s" % "；".join(problems))
        else:
            ok, err = (send_fn or send_message)(token, chat_id, html, False)
            if ok:
                sent = True
                state["last_sent_date"] = date
                delivery_mod.clear_held(state_dir, now_dt)
            else:
                errors.append(err or "发送失败")  # 文件已生成；last_generated_date 不写入，10 分钟后重试发送
    else:
        delivery_mod.clear_held(state_dir, now_dt)

    if not errors:
        state["last_generated_date"] = date
    save_state(state_dir, state)
    common.log_line("日报已生成 %s%s" % (path, "（已推送）" if sent else ""))
    return {"generated": generated, "sent": sent, "path": path, "errors": errors}
