#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py —— Telegram 推送（P0/P1）

通用能力（凭据读取、消息模板、发送、去重状态）在共享库 `libs/crypto_core`；
本文件只保留本模块的命令行流程与默认路径。

用法：
    python3 scripts/notify.py --dry-run      只打印，不发送（默认就是它）
    python3 scripts/notify.py --test         发一条测试消息到你的 chat
    python3 scripts/notify.py --discover     从 getUpdates 自动补全 CHAT_ID 写入 .env
    python3 scripts/notify.py --send         真实发送今日 P0/P1（自动去重）
    python3 scripts/notify.py --send --level P0 --force

凭据：仓库根 .env（多个模块共用；模块内 .env 可覆盖，已在 .gitignore 中）
    TELEGRAM_BOT_TOKEN=<bot_token>
    TELEGRAM_CHAT_ID=<your_chat_id>
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ROOT,
    STATE_DIR,
    load_config,
    safe_json_dump,
    safe_json_load,
    sha256,
    today_str,
    ts_iso,
)
from crypto_core import env as _env  # noqa: E402
from crypto_core import events as _events  # noqa: E402
from crypto_core.dedupe import DailyKeys  # noqa: E402
from crypto_core.notify import MAX_LEN as MAX_LEN  # noqa: E402,F401 - 兼容旧接口
from crypto_core.notify import (  # noqa: E402
    TOKEN_RE,
    check_credentials,
    discover_chat_id,
    format_alert,
    send_message,
)
from crypto_core.notify.templates import esc as esc  # noqa: E402,F401 - 兼容旧接口
from crypto_core.timeutil import in_quiet_hours  # noqa: E402

# 稳定对外接口（tests/外部脚本按这些名字调用）
__all__ = [
    "MAX_LEN",
    "TOKEN_RE",
    "esc",
    "format_alert",
    "check_credentials",
    "discover_chat_id",
    "send_message",
    "load_env",
    "save_env_value",
    "event_key",
    "in_quiet_hours",
    "quiet_span",
    "main",
]

ENV_PATH = _env.find_env_file(module_dir=ROOT)
NOTIFIED_PATH = os.path.join(STATE_DIR, "notified.json")
TRANSLATE_CACHE = os.path.join(STATE_DIR, "translate-cache.json")
TRANSLATE_SCRIPT = os.path.join(os.path.dirname(ROOT), "tools", "translate_titles.py")
DEFAULT_QUIET_SPAN = "22:00-07:00"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _needs_translate(alert):
    title = (alert.get("titles") or [""])[0] or alert.get("title") or ""
    return bool(title) and not CJK_RE.search(title)


def translate_alerts(todo):
    """英语标题优先走 AI 翻译（批量一次、带缓存）；失败保留规则版 cn_summary。"""
    if os.environ.get("CRYPTO_TRANSLATE") == "0":
        return
    cfg = load_config("notify.yaml").get("translate") or {}
    if not cfg.get("enabled", True):
        return
    cache = safe_json_load(TRANSLATE_CACHE, {})
    pending, keys = [], []
    for _, a in todo:
        if not _needs_translate(a):
            continue
        title = (a.get("titles") or [""])[0]
        h = sha256(title)
        if cache.get(h):
            a["cn_summary"] = cache[h]
            continue
        pending.append(title)
        keys.append((a, h))
    if not pending:
        return
    timeout = int(cfg.get("timeout", 45))
    try:
        proc = subprocess.run(
            [sys.executable, TRANSLATE_SCRIPT, "--timeout", str(timeout)],
            input=json.dumps(pending, ensure_ascii=False),
            text=True, capture_output=True, timeout=timeout + 15,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        data = {}
    for (a, h), text in zip(keys, data.get("translations") or []):
        if text:
            a["cn_summary"] = text
            cache[h] = text
    cap = int(cfg.get("cache_size", 500))
    if len(cache) > cap:
        cache = dict(list(cache.items())[-cap:])
    safe_json_dump(TRANSLATE_CACHE, cache)


# ---------------------------------------------------------------- 凭据（薄封装，保留旧接口）
def load_env(path=None):
    return _env.load_env(path or ENV_PATH)


def save_env_value(key, value, path=None):
    return _env.save_env_value(key, value, path or ENV_PATH)


# ---------------------------------------------------------------- 去重（旧接口保留）
def event_key(alert):
    return _events.event_key(alert)


def _norm_title(title):
    return _events.text.norm_title(title)


def quiet_span():
    delivery = load_config("thresholds.yaml").get("delivery", {})
    return delivery.get("quiet_hours", DEFAULT_QUIET_SPAN)


def quiet_enabled():
    """夜间静默总开关；设计约束全天即时，默认关闭时不再压制 P1。"""
    delivery = load_config("thresholds.yaml").get("delivery", {})
    return bool(delivery.get("quiet_enabled", True))


# ---------------------------------------------------------------- 主流程
def main():
    args = sys.argv[1:]
    dry_run = True
    do_test = False
    do_discover = False
    do_check = False
    level = None
    date = today_str()
    force = False
    for i, a in enumerate(args):
        if a == "--send":
            dry_run = False
        elif a == "--test":
            do_test = True
            dry_run = False
        elif a == "--discover":
            do_discover = True
            dry_run = False
        elif a == "--check":
            do_check = True
        elif a == "--dry-run":
            dry_run = True
        elif a == "--force":
            force = True
        elif a == "--level" and i + 1 < len(args):
            level = args[i + 1].upper()
        elif a == "--date" and i + 1 < len(args):
            date = args[i + 1]

    env = load_env()
    token, chat_id, problems = check_credentials(env)

    if do_check:
        print("token :", "✅ 格式正确" if not problems or "TOKEN" not in " ".join(problems) else "❌ 未填或格式错误")
        print("chat_id:", "✅ 已填" if str(chat_id).lstrip("-").isdigit() else "❌ 未填（可用 --discover 自动补）")
        if problems:
            print("\n还差：")
            for p in problems:
                print(" -", p)
            print("\n请编辑仓库根的 .env（不是 env.example），填好后保存。")
            return 2
        print("\n凭据就绪，可以 --test 或 --send 了。")
        return 0

    if not dry_run and problems and not do_discover:
        print("凭据不完整，无法发送：")
        for p in problems:
            print(" -", p)
        print("请编辑仓库根 .env，填入真实值：")
        print("  TELEGRAM_BOT_TOKEN=<bot_token>")
        print("  TELEGRAM_CHAT_ID=<your_chat_id>")
        return 2

    if do_discover:
        if not TOKEN_RE.match(token):
            print("TELEGRAM_BOT_TOKEN 未填或格式不对，无法自动发现 CHAT_ID。")
            print("请先在 .env 里填真实 token（形如 123456789:AAH...），保存后再试。")
            return 2
        try:
            found = discover_chat_id(token)
        except Exception as e:  # noqa: BLE001 - 网络错误直接提示
            print("调用 getUpdates 失败：%s" % e)
            return 2
        if not found:
            print("没有拿到 chat_id。请先在 Telegram 里给你的 Bot 发一条消息（比如 hi），再重试。")
            return 2
        save_env_value("TELEGRAM_CHAT_ID", found)
        print("已自动写入 TELEGRAM_CHAT_ID=%s 到 .env" % found)
        return 0

    alerts_path = os.path.join(STATE_DIR, "alerts-%s.json" % date)
    if not os.path.exists(alerts_path):
        print("没有找到 %s，请先跑 score.py" % alerts_path)
        return 1
    alerts = safe_json_load(alerts_path, {"items": []})
    items = alerts.get("items", [])
    if level:
        items = [x for x in items if x.get("level") == level]

    notified = DailyKeys(NOTIFIED_PATH, date)
    quiet = quiet_enabled() and in_quiet_hours(span=quiet_span())
    todo = []
    for a in items:
        k = event_key(a)
        if notified.seen(k):
            continue
        if quiet and a.get("level") == "P1" and not force:
            continue
        todo.append((k, a))

    if do_test:
        text = (
            "✅【crypto-news】<b>通道测试</b>\n"
            "通知级别：P0 即时 / P1 小时级 / P2 日报\n"
            "时间：%s" % ts_iso()
        )
        if dry_run:
            print("[dry-run] 测试消息：\n" + text)
            return 0
        ok, err = send_message(token, chat_id, text)
        print("测试消息%s%s" % ("发送成功 ✅" if ok else "发送失败 ❌", (" | " + err) if err else ""))
        return 0 if ok else 2

    translate_alerts(todo)

    print("今日待推送：%d 条（P0/P1 合计 %d 条，已过滤已推送）" % (len(todo), len(items)))
    sent = 0
    for k, a in todo:
        a.setdefault("module", "crypto-news")
        text = format_alert(a)
        if dry_run:
            print("\n---- %s ----\n%s" % (a.get("level"), text))
        else:
            ok, err = send_message(token, chat_id, text)
            if ok:
                notified.mark(k)
                sent += 1
                print("  ✅ %s %s" % (a.get("level"), (a.get("titles") or [""])[0][:50]))
            else:
                print("  ❌ 发送失败：%s" % err)
            time.sleep(0.6)

    if not dry_run:
        notified.save()
        print("已发送 %d 条" % sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
