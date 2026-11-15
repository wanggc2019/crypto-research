# -*- coding: utf-8 -*-
"""全仓推送台账：每条 Telegram 发送（含被预算拦截的）追加一行到
<repo>/data/state/notify-ledger/<YYYY-MM-DD>.jsonl（CRYPTO_NOTIFY_LEDGER_DIR 可覆盖）。

用途：回答「今天实际打扰了几次、谁最吵、哪些是跨模块重复」，并作为全局预算（budget.py）的计数来源。
只记元信息 + 首行预览（本地 data/，不入库、不外发）；写失败绝不影响发送。
"""
import datetime
import fcntl
import json
import os
import re
import sys
import tempfile
import time

from .. import paths

KINDS = ("signal", "report", "health", "lesson", "info", "test", "other")
_MODULE_RE = re.compile(r"【(crypto-[a-z0-9-]+)】")


def ledger_dir():
    explicit = os.environ.get("CRYPTO_NOTIFY_LEDGER_DIR")
    if explicit:
        return explicit
    if "unittest" in sys.modules:   # 保险：单测里没显式指定时绝不写生产台账（它是全局预算的计数来源）
        return os.path.join(tempfile.gettempdir(), "crypto-notify-ledger-unittest")
    return str(paths.repo_root() / "data" / "state" / "notify-ledger")


def infer_module(text=""):
    """调用方没给 module 时推断：CRYPTO_JOB_LABEL（com.crypto-research.<module>[.suffix]）→ 文本里的【crypto-xxx】。"""
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label.startswith("com.crypto-research."):
        rest = label[len("com.crypto-research."):]
        if rest.startswith("crypto-"):
            return rest.split(".", 1)[0]
        return rest.split(".", 1)[0] or "unknown"
    m = _MODULE_RE.search(str(text or "")[:200])
    return m.group(1) if m else "unknown"


def _preview(text):
    first = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    return re.sub(r"<[^>]+>", "", first)[:80]


def record(meta=None, ok=False, err="", text="", decision=None, now=None):
    """追加一条台账；返回写入的 dict（写失败返回 None）。"""
    meta = dict(meta or {})
    ts = now if now is not None else time.time()
    entry = {
        "ts": datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
        "epoch": round(ts, 3),
        "module": meta.get("module") or infer_module(text),
        "kind": meta.get("kind") if meta.get("kind") in KINDS else "other",
        "severity": meta.get("severity"),
        "assets": sorted({str(a).upper() for a in (meta.get("assets") or []) if a}),
        "key": meta.get("key"),
        "ok": bool(ok),
        "err": str(err or "")[:160],
        "chars": len(str(text or "")),
        "preview": _preview(text),
    }
    if decision:
        entry["decision"] = decision
    try:
        directory = ledger_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, entry["ts"][:10] + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except OSError:
        return None


def read(date):
    """读某天（YYYY-MM-DD）的全部台账；坏行跳过。"""
    path = os.path.join(ledger_dir(), "%s.jsonl" % date)
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def delivered(entry):
    """真正送达用户的一条（发送成功且未被预算拦截）。"""
    return bool(entry.get("ok")) and (entry.get("decision") or {}).get("action") != "suppress"
