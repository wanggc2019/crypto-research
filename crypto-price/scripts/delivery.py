#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""delivery.py —— 价格告警的送达治理：分级、静默时段、去重/冷却、升级、限额、重试。

职责边界：
- 输入：rules.evaluate() 产出的事件 dict（见 docs/crypto-price/design.md 的接口契约）
- 输出：Telegram 推送 + 本地审计（alerts-<date>.json）+ 状态（delivery.json）
- 不做：行情抓取、规则判定、报告生成（分别由 sources/rules/report 负责）

关键行为：
- P0：立即送达，可穿透静默；P1：日间送达，静默/限额时压制到早报；P2：只落盘。
- 去重键为事件自带 dedupe_key；冷却按规则配置；幅度继续扩大 >=escalate_ratio 或等级升级可绕过冷却。
- 发送失败进入 pending 重试队列（最多 5 次 / 1 小时），避免一次网络抖动丢告警。
- 状态原子写；dry-run 只读不写、不发送。
"""
import datetime
import os

import cards

import common
from crypto_core import timeutil
from crypto_core.notify import check_credentials, send_message

SEV_RANK = {"P0": 0, "P1": 1, "P2": 2}
DEFAULT_COOLDOWN_MIN = 30
MAX_PENDING = 30
MAX_PENDING_ATTEMPTS = 5
PENDING_TTL_SEC = 3600


# ---------------------------------------------------------------- 时间/状态
def _as_dt(now=None):
    if now is None:
        return timeutil.now()
    if isinstance(now, datetime.datetime):
        return now
    return datetime.datetime.fromtimestamp(float(now))


def _state_path(state_dir):
    return os.path.join(str(state_dir), "delivery.json")


def _alerts_path(state_dir, date):
    return os.path.join(str(state_dir), "alerts-%s.json" % date)


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_key(event):
    return (event or {}).get("dedupe_key") or (event or {}).get("title") or ""


def default_state(now=None):
    return {
        "date": timeutil.today_str(_as_dt(now)),
        "counts": {"P0": 0, "P1": 0, "P2": 0},
        "cooldowns": {},
        "held": [],
        "pending": [],
        "last_sent_ts": 0,
    }


def load_state(state_dir, now=None):
    """读投递状态；坏文件/坏字段一律回退默认值（容忍合法 JSON 但类型损坏）。"""
    raw = common.safe_json_load(_state_path(state_dir), {})
    state = default_state(now)
    if not isinstance(raw, dict):
        return state
    state.update({k: raw[k] for k in raw if k in state})

    raw_counts = _as_dict(raw.get("counts"))
    state["counts"] = {sev: max(0, _to_int(raw_counts.get(sev), 0)) for sev in SEV_RANK}

    raw_cooldowns = _as_dict(raw.get("cooldowns"))
    state["cooldowns"] = {}
    for key, item in raw_cooldowns.items():
        item = _as_dict(item)
        ts = _to_float(item.get("ts"), 0.0)
        if ts > 0:
            state["cooldowns"][str(key)] = {
                "ts": ts,
                "magnitude": _to_float(item.get("magnitude"), 0.0),
                "severity": item.get("severity") if item.get("severity") in SEV_RANK else "P2",
                "rule": str(item.get("rule") or ""),
            }

    state["held"] = []
    for item in _as_list(raw.get("held")):
        item = _as_dict(item)
        event = _as_dict(item.get("event"))
        if not event:
            continue
        state["held"].append({
            "event": event,
            "reason": str(item.get("reason") or ""),
            "held_ts": _to_float(item.get("held_ts"), 0.0),
        })

    state["pending"] = []
    for item in _as_list(raw.get("pending")):
        item = _as_dict(item)
        event = _as_dict(item.get("event"))
        if not event:
            continue
        state["pending"].append({
            "event": event,
            "attempts": max(0, _to_int(item.get("attempts"), 0)),
            "created_ts": _to_float(item.get("created_ts"), 0.0),
            "next_retry_ts": _to_float(item.get("next_retry_ts"), 0.0),
        })

    # 跨天：计数归零；cooldowns/held/pending 保留（静默跨零点、重试可能跨天）
    today = timeutil.today_str(_as_dt(now))
    if state.get("date") != today:
        state["date"] = today
        state["counts"] = {sev: 0 for sev in SEV_RANK}
    now_ts = int(_as_dt(now).timestamp())
    state["cooldowns"] = {
        k: v for k, v in state["cooldowns"].items()
        if now_ts - v["ts"] < 48 * 3600
    }
    fresh_pending = {}
    for p in state["pending"]:
        if now_ts - (p["created_ts"] or now_ts) < PENDING_TTL_SEC:
            fresh_pending[_event_key(p.get("event"))] = p
    state["pending"] = list(fresh_pending.values())
    # held 最多保留 24h / 100 条，并按 dedupe_key 去重（历史坏数据也兜底）
    seen = {}
    for item in state["held"]:
        if now_ts - (item["held_ts"] or now_ts) >= 24 * 3600:
            continue
        seen[_event_key(item["event"])] = item
    state["held"] = list(seen.values())[-100:]
    state["last_sent_ts"] = _to_float(raw.get("last_sent_ts"), 0.0)
    return state


def save_state(state_dir, state):
    common.safe_json_dump(_state_path(state_dir), state)
    return state


def _append_alert_record(state_dir, alert, now_dt=None):
    """审计：当天所有判定结果（含未推送）追加到 alerts-<date>.json，最多保留 500 条。"""
    date = timeutil.today_str(now_dt)
    path = _alerts_path(state_dir, date)
    rows = common.safe_json_load(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(alert)
    common.safe_json_dump(path, rows[-500:])
    return path


# ---------------------------------------------------------------- 事件 → alert/v1
def event_to_alert(event, now=None, poll_seconds=None):
    """把规则事件转成统一 alert/v1 结构（供卡片/审计/未来报告消费）。"""
    sev = event.get("severity") or "P1"
    asset = event.get("asset") or "ALL"
    metrics = event.get("metrics") or {}
    alert = {
        "schema": "alert/v1",
        "module": "crypto-price",
        "severity": sev,
        "category": "price",
        "assets": [asset] if asset and asset != "ALL" else [],
        "title": event.get("title") or "",
        "detail": list(event.get("detail") or []),
        "urls": list(event.get("urls") or []),
        "ts": event.get("ts") or timeutil.ts_iso(_as_dt(now)),
        "dedupe_key": event.get("dedupe_key") or "",
        "score": {"total": round(float(event.get("magnitude") or 0), 2)},
        "summary": {
            "fact": event.get("title") or "",
            "why": event.get("why") or "",
            "action": event.get("action") or "",
        },
        "evidence": list(event.get("evidence") or []),
        "rule": event.get("rule") or "",
        "direction": event.get("direction") or "flat",
        "metrics": metrics,
        "escalated": bool(event.get("escalated")),
        "simulated": bool(event.get("simulated")),
        "source_health": event.get("source_health") or {},
        "primary_sources": event.get("primary_sources") or [],
    }
    if poll_seconds:
        alert["poll_seconds"] = int(poll_seconds)
    return alert


# ---------------------------------------------------------------- 决策
def _cooldown_minutes(rule, delivery_cfg):
    table = delivery_cfg.get("cooldown_minutes") or {}
    return float(table.get(rule, table.get("default", DEFAULT_COOLDOWN_MIN)))


def _decide(event, state, delivery_cfg, now_dt):
    """返回 (decision, reason)。

    decision: send / hold / suppress / archive
    reason: 人类可读原因，写入审计。
    """
    sev = event.get("severity") or "P1"
    rule = event.get("rule") or "unknown"
    key = event.get("dedupe_key") or ""
    now_ts = int(now_dt.timestamp())

    if sev == "P2":
        return "archive", "P2 只归档不推送"

    # 静默时段：除 quiet_exception 或 force_send 外，全部压制到早报
    quiet_span = delivery_cfg.get("quiet_hours")
    if quiet_span is None:
        quiet_span = timeutil.DEFAULT_QUIET_SPAN
    exceptions = set(delivery_cfg.get("quiet_exception") or ["P0"])
    if (timeutil.in_quiet_hours(now_dt, quiet_span)
            and sev not in exceptions
            and not event.get("force_send")):
        return "hold", "静默时段（%s）压制到早报" % quiet_span

    # 发送失败重试冷却：同一事件在 pending 的 next_retry_ts 之前不再尝试（防网络故障时每轮风暴）
    for p in state.get("pending") or []:
        if _event_key(p.get("event")) != key:
            continue
        if now_ts < _to_int(p.get("next_retry_ts"), 0):
            return "suppress", "发送失败，重试冷却中（第 %d 次）" % _to_int(p.get("attempts"), 0)

    # 冷却 / 升级
    cd = state["cooldowns"].get(key) if key else None
    if cd:
        elapsed = now_ts - float(cd.get("ts", 0) or 0)
        minutes = _cooldown_minutes(rule, delivery_cfg)
        if elapsed < minutes * 60:
            prev_mag = float(cd.get("magnitude", 0) or 0)
            new_mag = float(event.get("magnitude", 0) or 0)
            ratio = float(delivery_cfg.get("escalate_ratio", 1.5) or 1.5)
            upgraded = SEV_RANK.get(sev, 9) < SEV_RANK.get(str(cd.get("severity", "P2")), 9)
            escalated = prev_mag > 0 and new_mag >= prev_mag * ratio
            if upgraded or escalated:
                event["escalated"] = True
                return "send", "冷却中，但%s，允许升级推送" % ("等级提升" if upgraded else "幅度扩大 ≥%.1f 倍" % ratio)
            return "suppress", "冷却中（%s 规则 %g 分钟，剩 %.0f 分钟）" % (
                rule, minutes, minutes - elapsed / 60.0)

    # 每日 P1 限额（P0 不限；force_send 的运维告警不受限额影响）
    if sev == "P1" and not event.get("force_send"):
        cap = int(delivery_cfg.get("daily_cap_p1", 20) or 0)
        if cap and state["counts"].get("P1", 0) >= cap:
            return "hold", "今日 P1 已达上限 %d 条，压制到早报" % cap

    return "send", ""


def select(events, state, delivery_cfg, now=None):
    """纯决策：对事件列表排序并给出 send/hold/suppress/archive 判定（不产生副作用）。"""
    now_dt = _as_dt(now)
    ordered = sorted(
        events or [],
        key=lambda e: (SEV_RANK.get(e.get("severity") or "P2", 9), -_to_float(e.get("magnitude"), 0.0)),
    )
    decisions = []
    for ev in ordered:
        decision, reason = _decide(ev, state, delivery_cfg, now_dt)
        decisions.append({"event": ev, "decision": decision, "reason": reason})
    return decisions


def _hold(state, event, reason, now_ts):
    """记录被压制事件；同一 dedupe_key 只保留一条（取更严重/幅度更大的版本）。"""
    key = _event_key(event)
    for item in state["held"]:
        if _event_key(item.get("event")) != key:
            continue
        old = item.get("event") or {}
        old_rank = SEV_RANK.get(old.get("severity") or "P2", 9)
        new_rank = SEV_RANK.get(event.get("severity") or "P2", 9)
        old_mag = _to_float(old.get("magnitude"), 0.0)
        new_mag = _to_float(event.get("magnitude"), 0.0)
        if new_rank < old_rank or new_mag > old_mag:
            item["event"] = event
        item["reason"] = reason
        item["held_ts"] = now_ts
        return
    state["held"].append({"event": event, "reason": reason, "held_ts": now_ts})
    state["held"] = state["held"][-100:]


def _record_delivery(alert, decision, reason):
    alert = dict(alert)
    alert["delivery"] = {"decision": decision, "reason": reason}
    return alert


# ---------------------------------------------------------------- 主入口
def deliver(events, rules_cfg, state_dir, env=None, now=None, dry_run=False,
            send_fn=None, poll_seconds=None):
    """治理并投递一轮事件。

    返回 {sent, held, suppressed, archived, errors, quiet, dry_run, counts}。
    - dry_run=True：只计算计划，不发送、不写状态/审计。
    - send_fn 可注入（测试用），签名 (token, chat_id, text, silent) -> (ok, error)。
    """
    env = env or common.load_env()
    send_fn = send_fn or send_message
    now_dt = _as_dt(now)
    now_ts = int(now_dt.timestamp())
    delivery_cfg = (rules_cfg or {}).get("delivery") or {}
    state = load_state(state_dir, now_dt)
    quiet_cfg = delivery_cfg.get("quiet_hours")
    quiet = timeutil.in_quiet_hours(
        now_dt, timeutil.DEFAULT_QUIET_SPAN if quiet_cfg is None else quiet_cfg)

    token, chat_id, problems = check_credentials(env)
    if problems and not dry_run:
        return {
            "sent": [], "held": [], "suppressed": [], "archived": 0,
            "errors": [{"error": "Telegram 凭据不完整：%s" % "；".join(problems)}],
            "quiet": quiet, "dry_run": dry_run, "counts": dict(state["counts"]),
        }

    # 重试队列：到期且未超次数的失败事件，合并进本轮（同 dedupe_key 以新一轮为准）
    pending_meta = {_event_key(p.get("event")): p for p in state["pending"]}
    retry_events, keep_pending = [], []
    for p in state["pending"]:
        ev = p.get("event") or {}
        due = now_ts >= _to_int(p.get("next_retry_ts"), 0)
        attempts = _to_int(p.get("attempts"), 0)
        if due and attempts < MAX_PENDING_ATTEMPTS:
            retry_events.append(ev)
        else:
            keep_pending.append(p)
    state["pending"] = keep_pending

    by_key = {}
    for ev in list(retry_events) + list(events or []):
        key = ev.get("dedupe_key") or ev.get("title") or ""
        if key not in by_key:
            by_key[key] = ev
        elif SEV_RANK.get(ev.get("severity") or "P2", 9) < SEV_RANK.get(by_key[key].get("severity") or "P2", 9):
            by_key[key] = ev
    merged = list(by_key.values())

    sent, held, suppressed, archived, errors = [], [], [], 0, []
    for item in select(merged, state, delivery_cfg, now_dt):
        ev = item["event"]
        decision = item["decision"]
        reason = item["reason"]
        alert = event_to_alert(ev, now_dt, poll_seconds=poll_seconds)
        alert = _record_delivery(alert, decision, reason)
        key = ev.get("dedupe_key") or ""

        if decision == "archive":
            archived += 1
            if not dry_run:
                _append_alert_record(state_dir, alert, now_dt)
            continue
        if decision == "hold":
            _hold(state, ev, reason, now_ts)
            held.append(alert)
            if not dry_run:
                _append_alert_record(state_dir, alert, now_dt)
            continue
        if decision == "suppress":
            suppressed.append(alert)
            if not dry_run:
                _append_alert_record(state_dir, alert, now_dt)
            continue

        # decision == send
        if dry_run:
            sent.append(alert)
            state["counts"][alert["severity"]] = state["counts"].get(alert["severity"], 0) + 1
            if key:
                state["cooldowns"][key] = {
                    "ts": now_ts, "magnitude": float(ev.get("magnitude") or 0),
                    "severity": alert["severity"], "rule": ev.get("rule") or "",
                }
            continue
        text = cards.format_price_alert(alert)
        # 价格告警保留 Telegram 链接预览（设计约束展示行情卡片）。
        ok, err = send_fn(token, chat_id, text, False)
        if ok:
            state["counts"][alert["severity"]] = state["counts"].get(alert["severity"], 0) + 1
            state["last_sent_ts"] = now_ts
            if key:
                state["cooldowns"][key] = {
                    "ts": now_ts,
                    "magnitude": float(ev.get("magnitude") or 0),
                    "severity": alert["severity"],
                    "rule": ev.get("rule") or "",
                }
            sent.append(alert)
            _append_alert_record(state_dir, alert, now_dt)
            common.log_line("告警已推送 %s %s %s" % (alert["severity"], " ".join(alert["assets"]) or "-",
                                                  alert["title"]))
        else:
            failed = _record_delivery(alert, "failed", err or "发送失败")
            errors.append({"dedupe_key": key, "error": err or "发送失败"})
            prev = pending_meta.get(key) or {}
            attempts = _to_int(prev.get("attempts"), 0) + 1
            created_ts = _to_float(prev.get("created_ts"), 0.0) or now_ts
            state["pending"] = [p for p in state["pending"] if _event_key(p.get("event")) != key]
            if attempts <= MAX_PENDING_ATTEMPTS and len(state["pending"]) < MAX_PENDING:
                state["pending"].append({
                    "event": ev,
                    "attempts": attempts,
                    "created_ts": created_ts,
                    "next_retry_ts": now_ts + 60 * attempts,
                })
            _append_alert_record(state_dir, failed, now_dt)

    if not dry_run:
        save_state(state_dir, state)

    return {
        "sent": sent,
        "held": held,
        "suppressed": suppressed,
        "archived": archived,
        "errors": errors,
        "quiet": quiet,
        "dry_run": dry_run,
        "counts": dict(state["counts"]),
    }


def held_events(state_dir, now=None):
    """早报用：读取被压制的 P1 事件（按时间排序）。"""
    state = load_state(state_dir, now)
    out = []
    for h in state.get("held") or []:
        ev = h.get("event") or {}
        if not ev:
            continue
        out.append({
            "ts": ev.get("ts") or "",
            "asset": ev.get("asset") or "",
            "severity": ev.get("severity") or "P1",
            "title": ev.get("title") or "",
            "reason": h.get("reason") or "",
        })
    return sorted(out, key=lambda x: x.get("ts") or "")


def clear_held(state_dir, now=None):
    state = load_state(state_dir, now)
    state["held"] = []
    save_state(state_dir, state)
    return state
