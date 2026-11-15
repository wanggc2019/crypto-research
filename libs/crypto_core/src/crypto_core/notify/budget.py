# -*- coding: utf-8 -*-
"""全局推送预算：所有模块的「即时信号」共用一个日预算 + 跨模块相关性去重。

配置 <repo>/config/notify-budget.yaml（缺省见 DEFAULTS）：
- mode: observe（默认：只在台账里标注 would_suppress，照常发送）| enforce（真拦截）| off
- daily_signal_cap: 每天送达的非 P0 信号上限（全仓合计）
- correlation_window_min: 同一资产在窗口内已有**其它模块**送达的信号 → 视为重复
- P0 永远放行（只计数）；报告/课程/健康检查等 kind≠signal 不受预算约束

被拦截（enforce）的信号返回 (True, "suppressed: …")：对调用方而言「已处理」，避免下一轮重试同一条；
被拦截的内容仍在各模块自己的日报/报告里（预算只管「即时打扰」）。
"""
import time

from .. import config, paths, timeutil
from . import ledger, telegram

WATCH = ("BTC", "ETH", "BNB")   # 关注范围（AGENTS §0）；相关性只在这些资产上判断


def watch_assets(values):
    """把各模块的 assets/tags 归一成关注资产集合（大小写不敏感，其它标签丢弃）。"""
    out = []
    for v in values or []:
        u = str(v or "").strip().upper()
        if u in WATCH and u not in out:
            out.append(u)
    return out


DEFAULTS = {"mode": "observe", "daily_signal_cap": 10, "correlation_window_min": 30}
_CONFIG_PATH = None


def load_config():
    path = _CONFIG_PATH or str(paths.repo_root() / "config" / "notify-budget.yaml")
    cfg = dict(DEFAULTS)
    try:
        data = config.load_yaml(path)
    except Exception:  # noqa: BLE001 - 配置坏了按默认（observe）跑，绝不因预算挡住发送
        data = {}
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS and v is not None})
    if cfg["mode"] not in ("observe", "enforce", "off"):
        cfg["mode"] = "observe"
    return cfg


def decide(module, severity, assets=(), now=None, cfg=None):
    """返回 {"mode", "action": send|suppress, "would_suppress", "reasons": [...], "related": [...], "today_signals"}。"""
    cfg = cfg or load_config()
    now = now if now is not None else time.time()
    mode = cfg["mode"]
    decision = {"mode": mode, "action": "send", "would_suppress": False, "reasons": [], "related": [],
                "today_signals": 0}
    if mode == "off":
        return decision
    today = [e for e in ledger.read(timeutil.today_str(_local(now)))
             if e.get("kind") == "signal" and ledger.delivered(e)]
    decision["today_signals"] = sum(1 for e in today if e.get("severity") != "P0")
    if str(severity or "").upper() == "P0":
        return decision
    reasons = decision["reasons"]
    cap = int(cfg["daily_signal_cap"])
    if decision["today_signals"] >= cap:
        reasons.append("daily_cap:%d/%d" % (decision["today_signals"], cap))
    wanted = set(watch_assets(assets))
    window = float(cfg["correlation_window_min"]) * 60
    for e in today:
        if e.get("module") == module or now - float(e.get("epoch") or 0) > window:
            continue
        overlap = wanted & set(e.get("assets") or [])
        if overlap:
            decision["related"].append({"module": e.get("module"), "assets": sorted(overlap),
                                        "ts": e.get("ts"), "preview": e.get("preview")})
    if decision["related"]:
        reasons.append("correlated:%s" % ",".join(sorted({r["module"] for r in decision["related"]})))
    decision["would_suppress"] = bool(reasons)
    if reasons and mode == "enforce":
        decision["action"] = "suppress"
    return decision


def _local(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts)


def send_signal(token, chat_id, text, module, severity, assets=(), key=None, now=None, sender=None,
                sender_args=(), **send_kwargs):
    """即时信号统一出口：预算判定 → 发送（或拦截）→ 记台账。返回 (ok, err)，拦截时 (True, "suppressed: …")。

    sender：可选的实际发送函数（模块里可被测试 patch 的 send_message / 注入的假实现），
    以 sender(token, chat_id, text, *sender_args, **send_kwargs) 调用；为空时用 telegram.send_message。
    """
    meta = {"module": module, "kind": "signal", "severity": severity, "assets": watch_assets(assets), "key": key}
    try:
        decision = decide(module, severity, assets, now=now)
    except Exception as e:  # noqa: BLE001 - 预算自身出错一律放行
        decision = {"mode": "error", "action": "send", "would_suppress": False, "reasons": ["budget_error:%s" % e]}
    if decision["action"] == "suppress":
        ledger.record(meta, ok=False, err="suppressed", text=text, decision=decision, now=now)
        return True, "suppressed: %s" % "; ".join(decision["reasons"])
    if sender is None or sender is telegram.send_message:
        return telegram.send_message(token, chat_id, text, *sender_args, meta=meta, decision=decision, **send_kwargs)
    ok, err = sender(token, chat_id, text, *sender_args, **send_kwargs)
    ledger.record(meta, ok=ok, err=err, text=text, decision=decision, now=now)
    return ok, err
