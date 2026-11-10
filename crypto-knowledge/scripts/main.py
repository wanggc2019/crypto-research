#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— crypto-knowledge 入口。

每天三个时段（本地时区）由 launchd 触发，collect.sh 用 --slot auto 选择：
  13:00  概念卡        （新概念，一条一个点）
  19:00  理论 / 实践    （交替编排，由浅入深）
  23:00  睡前复盘      （今日回顾 + 间隔复习 + 明日预告）

用法：
    python3 scripts/main.py --dry-run              # 只渲染，不写盘不推送
    python3 scripts/main.py --slot 13 --dry-run    # 指定时段预览
    python3 scripts/main.py --notify               # 按当前时段发送（collect.sh 用）
    python3 scripts/main.py --test                 # Telegram 通道测试
    python3 scripts/main.py --status               # 进度/内容库存
    python3 scripts/main.py --date 2026-11-10 --slot 13 --notify --force   # 补发

退出码：0 成功 / 1 部分失败 / 2 配置或内容错误 / 3 网络或凭据错误 / 4 锁被占用（collect.sh）。
"""
import argparse
import datetime
import json
import os
import sys

import content
import render
import schedule

import common

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_NETWORK = 3

SLOT_LABELS = {"13": "概念课", "19": "理论 / 实践", "21": "今日回顾与总结", "23": "睡前复盘课"}


def slot_for_hour(hour):
    """当前小时落在哪个时段附近（仅用于正常到点触发时的映射）。"""
    if 12 <= hour < 15:
        return "13"
    if 18 <= hour < 20:
        return "19"
    if 20 <= hour < 22:
        return "21"
    if 22 <= hour <= 23:
        return "23"
    return None


def resolve_slot(value, now_dt, state, date):
    """显式时段优先；auto 时先补发今天已到点未发的，再按当前窗口映射。

    设计约束：消息及时发，不管夜间/休息时间；Mac 睡醒后错过时段也补发，
    不再「不在时段就跳过」。
    """
    if value and value != "auto":
        if value not in schedule.SLOTS:
            raise SystemExit("--slot 只能是 13 / 19 / 21 / 23 / auto")
        return value
    due = [s for s in schedule.SLOTS
           if schedule.slot_hour(s) <= now_dt.hour
           and not schedule.already_sent(state, date, s)]
    if due:
        return due[0]
    mapped = slot_for_hour(now_dt.hour)
    if mapped:
        return mapped
    return None


def make_ctx(item, state, date, job_label):
    item = item or {}
    return {
        "brand": "crypto-knowledge",
        "job_label": job_label,
        "day_no": schedule.day_number(state, date),
        "stage": int(item.get("stage") or 1),
        "stage_title": item.get("_stage_title") or "",
    }


def build_resend(state, items, slot, date, job_label):
    """--force 重发：按当日已发记录重新渲染同一条内容，不推进游标/SRS。"""
    ids = ((state.get("sent") or {}).get(date) or {}).get(slot) or []
    by_id = {it["id"]: it for it in items}
    chosen = [by_id[i] for i in ids if i in by_id]
    if not chosen:
        return None
    ctx = make_ctx(chosen[0], state, date, job_label)
    if slot == "23":
        concepts, lessons = schedule.split_tracks(items)
        today = schedule.today_items(state, items, date)
        return {"text": render.render_review(ctx, today, [], schedule.preview(state, concepts, lessons)),
                "items": [], "mode": "review", "track": None, "resend": True}
    if slot == "13":
        text = render.render_concept(chosen[0], ctx)
    else:
        text = render.render_lesson(chosen[0], ctx)
    return {"text": text, "items": [], "mode": "resend", "track": None, "resend": True}


def _weekly_due(cfg, date):
    weekly = cfg.get("weekly") or {}
    if not weekly.get("enabled", True):
        return False
    try:
        weekday = int(weekly.get("weekday") or 7)
    except (TypeError, ValueError):
        weekday = 7
    return schedule.is_weekday(date, weekday)


def build_weekly(state, items, date, job_label):
    concepts, lessons = schedule.split_tracks(items)
    rows = schedule.week_log(state, items, date)
    stats = schedule.recap_stats(state, items, date)
    active = schedule.week_active_days(state, date)
    stages = schedule.stage_progress(state, items)
    preview = schedule.preview(state, concepts, lessons)
    anchor = None
    for row in reversed(rows):
        if row["items"]:
            anchor = row["items"][-1]
            break
    ctx = make_ctx(anchor, state, date, job_label)
    return {"text": render.render_weekly(ctx, rows, stats, active, stages, preview),
            "items": [], "mode": "weekly", "track": None}


def build(state, items, slot, date, job_label, cfg, force_weekly=False):
    """返回 {text, items, mode, track}；没有可用内容返回 None。"""
    concepts, lessons = schedule.split_tracks(items)
    if slot == "13":
        pick = schedule.pick_concept(state, concepts, date)
        if not pick:
            return None
        ctx = make_ctx(pick["item"], state, date, job_label)
        return {"text": render.render_concept(pick["item"], ctx),
                "items": [pick["item"]], "mode": pick["mode"], "track": "concept"}
    if slot == "19":
        pick = schedule.pick_lesson(state, lessons, date)
        if not pick:
            return None
        ctx = make_ctx(pick["item"], state, date, job_label)
        return {"text": render.render_lesson(pick["item"], ctx),
                "items": [pick["item"]], "mode": pick["mode"], "track": "lesson"}
    if slot == "21" and (force_weekly or _weekly_due(cfg, date)):
        return build_weekly(state, items, date, job_label)
    if slot == "21":
        today = schedule.today_items(state, items, date)
        stats = schedule.recap_stats(state, items, date)
        preview = schedule.preview(state, concepts, lessons)
        anchor = today[0] if today else None
        ctx = make_ctx(anchor, state, date, job_label)
        return {"text": render.render_recap(ctx, today, stats, preview),
                "items": [], "mode": "recap", "track": None}
    if slot == "23":
        max_items = int((cfg.get("review_max_items") or 2))
        today, due = schedule.pick_review(state, items, date, max_items)
        preview = schedule.preview(state, concepts, lessons)
        anchor = today[0] if today else (due[0] if due else None)
        ctx = make_ctx(anchor, state, date, job_label)
        return {"text": render.render_review(ctx, today, due, preview),
                "items": due, "mode": "review", "track": None}
    return None


def _log(text, data_dir):
    """只把真实运行的日志写到模块 data/logs；测试用临时目录时不污染生产日志。"""
    if os.path.abspath(data_dir) == os.path.abspath(common.DATA_DIR):
        common.log_line(text)


def send(env, text, silent=False):
    token, chat_id, problems = common.core_tg.check_credentials(env)
    if problems:
        return False, "；".join(problems)
    return common.core_tg.send_message(token, chat_id, text, silent=silent, disable_preview=True)


def write_report(data_dir, date, slot, text, label=None):
    folder = os.path.join(data_dir, "reports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, date + ".md")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n## %s %s\n\n%s\n" % (date, label or SLOT_LABELS[slot], text))


def status_payload(items, state, date):
    p = schedule.progress(state, items, date)
    concepts, lessons = schedule.split_tracks(items)
    preview = schedule.preview(state, concepts, lessons)
    due = schedule.due_items(state, items, date)
    return {
        "progress": p,
        "inventory": content.inventory(items),
        "due": [{"id": it["id"], "title": it["title"]} for it in due[:10]],
        "preview": {key: (val["title"] if val else None) for key, val in preview.items()},
    }


def print_status(items, state, date, args):
    payload = status_payload(items, state, date)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    p = payload["progress"]
    inv = payload["inventory"]
    print("crypto-knowledge 状态")
    print("  今天 %s · 第 %d 天" % (p["date"], p["day_no"]))
    print("  内容 %d 条" % p["total"])
    for stage in sorted(inv):
        b = inv[stage]
        print("    阶段 %d：概念 %d / 理论 %d / 实践 %d"
              % (stage, b["concept"], b["theory"], b["practice"]))
    print("  进度 概念 %d/%d · 课程 %d/%d"
          % (p["concept_cursor"], p["concept_total"], p["lesson_cursor"], p["lesson_total"]))
    print("  待复习 %d 条" % p["due"])
    print("  今日已发 13 %s / 19 %s / 21 %s / 23 %s"
          % ("✓" if p["sent"]["13"] else "–",
             "✓" if p["sent"]["19"] else "–",
             "✓" if p["sent"]["21"] else "–",
             "✓" if p["sent"]["23"] else "–"))
    preview = payload["preview"]
    print("  下一条 概念：%s / 课程：%s"
          % (preview.get("concept") or "—", preview.get("lesson") or "—"))
    return EXIT_OK


def _mask(value, keep=6):
    text = str(value or "")
    if len(text) <= keep * 2:
        return text[:2] + "***" if text else ""
    return text[:keep] + "…" + text[-4:]


def whoami():
    """显示 crypto-knowledge 实际使用的 Bot 凭据来源（token 打码）。"""
    info = common.resolve_bot_env(common.load_env())
    token, chat = info["TELEGRAM_BOT_TOKEN"], info["TELEGRAM_CHAT_ID"]
    print("crypto-knowledge Telegram 凭据")
    if not token:
        print("  ❌ 未配置 Bot token：请在 .env 填 TELEGRAM_KNOWLEDGE_BOT_TOKEN（或回退的 TELEGRAM_BOT_TOKEN）")
        return EXIT_CONFIG
    print("  token：%s（来自 %s）" % (_mask(token), info["token_key"]))
    print("  chat ：%s（来自 %s）" % (_mask(chat), info["chat_key"]))
    print("  专用 Bot：%s" % ("是 ✅" if info["dedicated"] else "否（回退共享 Bot）"))
    try:
        resp = common.core_tg.tg_call(token, "getMe", {})
    except Exception as exc:  # noqa: BLE001 - 网络/凭据错误统一提示
        print("  ❌ getMe 调用失败：%s" % exc)
        return EXIT_NETWORK
    if not resp.get("ok"):
        print("  ❌ getMe 返回错误：%s" % resp.get("description"))
        return EXIT_CONFIG
    bot = resp.get("result") or {}
    print("  机器人：@%s（%s）" % (bot.get("username"), bot.get("first_name")))
    expected = os.environ.get("TELEGRAM_KNOWLEDGE_BOT_USERNAME", "")
    if expected and info["dedicated"] and (bot.get("username") or "").lower() != expected.lstrip("@").lower():
        print("  ⚠️ 专用 token 对应的机器人不是 %s，请检查是否填错" % expected)
    return EXIT_OK


def send_test(items, state, date, job_label):
    concepts, lessons = schedule.split_tracks(items)
    inv = content.inventory(items)
    pick = schedule.pick_concept(state, concepts, date)
    lesson = schedule.pick_lesson(state, lessons, date)
    lines = [
        "🧪<b>【crypto-knowledge】</b>通道测试",
        "🏷 任务：%s" % common.esc(job_label),
        render.SEP,
        "内容库：%d 条" % len(items),
    ]
    for stage in sorted(inv):
        b = inv[stage]
        lines.append("· 阶段 %d：概念 %d / 理论 %d / 实践 %d"
                     % (stage, b["concept"], b["theory"], b["practice"]))
    if pick:
        lines.append("下一条概念：%s" % common.esc(pick["item"]["title"]))
    if lesson:
        lines.append("下一条课程：%s" % common.esc(lesson["item"]["title"]))
    env = common.load_bot_env()
    ok, err = send(env, "\n".join(lines), silent=False)
    if ok:
        print("测试消息已发送 ✅")
        return EXIT_OK
    print("测试消息发送失败：%s" % err)
    return EXIT_NETWORK


def run(argv=None):
    parser = argparse.ArgumentParser(description="crypto-knowledge 每日学习内容")
    parser.add_argument("--slot", default="auto", choices=["13", "19", "21", "23", "auto"])
    parser.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天，用于补发/测试）")
    parser.add_argument("--dry-run", action="store_true", help="只渲染，不写盘、不推送、不推进状态")
    parser.add_argument("--notify", action="store_true", help="发送 Telegram 并推进状态")
    parser.add_argument("--test", action="store_true", help="发送一条通道测试")
    parser.add_argument("--status", action="store_true", help="查看进度与内容库存")
    parser.add_argument("--whoami", action="store_true", help="查看实际使用的 Telegram Bot（token 打码）")
    parser.add_argument("--weekly", action="store_true", help="强制渲染本周回顾（预览/补发用）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--force", action="store_true", help="同日同时段允许重发")
    parser.add_argument("--content", default=None, help="课程内容目录（默认 config/curriculum）")
    parser.add_argument("--fixtures", default=None, help="离线内容目录（CI 用，等价 --content）")
    parser.add_argument("--data-dir", default=None, help="数据目录（测试用临时目录）")
    args = parser.parse_args(argv)

    now_dt = datetime.datetime.now()
    date = args.date or common.today_str(now_dt)
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        print("--date 需要 YYYY-MM-DD：%r" % date)
        return EXIT_CONFIG

    data_dir = os.path.abspath(args.data_dir or common.DATA_DIR)
    state_path = os.path.join(data_dir, "state", "progress.json")

    if args.whoami:
        return whoami()

    cfg = common.load_config("app.yaml") or {}
    if cfg.get("enabled") is False:
        print("app.yaml enabled=false，已停用")
        return EXIT_OK

    content_dir = args.content or args.fixtures or common.CURRICULUM_DIR
    items = content.load_all(content_dir)
    if not items:
        print("没有课程内容：%s" % content_dir)
        return EXIT_CONFIG
    try:
        content.validate_or_raise(items)
    except ValueError as exc:
        print(str(exc))
        return EXIT_CONFIG

    job_label = common.job_label()
    state = schedule.normalize(common.load_state(state_path), date)
    slot = resolve_slot(args.slot, now_dt, state, date)

    if args.status:
        return print_status(items, state, date, args)
    if args.test:
        return send_test(items, state, date, job_label)

    if slot is None:
        if now_dt.hour < schedule.slot_hour("13"):
            print("%s 还没到第一班（13:00），等待下一班" % now_dt.strftime("%H:%M"))
        else:
            print("%s 今天所有到点时段都已发送" % date)
        return EXIT_OK

    if schedule.already_sent(state, date, slot):
        if not args.force:
            print("%s %s 已发送，跳过（--force 可重发）" % (date, SLOT_LABELS[slot]))
            return EXIT_OK
        plan = build_resend(state, items, slot, date, job_label)
    else:
        plan = build(state, items, slot, date, job_label, cfg, force_weekly=args.weekly)
    if not plan:
        print("没有可用内容（内容库为空？）")
        return EXIT_CONFIG

    max_chars = int((cfg.get("message") or {}).get("max_chars") or 3500)
    text = render.truncate(plan["text"], max_chars)
    ids = [it["id"] for it in plan["items"]]

    if args.json:
        print(json.dumps({"date": date, "slot": slot, "mode": plan["mode"],
                          "ids": ids, "chars": len(text)}, ensure_ascii=False))
    if args.dry_run:
        print(text)
        return EXIT_OK

    if args.notify:
        ok, err = send(common.load_bot_env(), text)
        if not ok:
            _log("send failed slot=%s: %s" % (slot, err), data_dir)
            print("发送失败：%s" % err)
            return EXIT_NETWORK
        if not plan.get("resend"):
            schedule.mark_sent(state, date, slot, plan, cfg.get("srs_days"))
            common.save_state(state, state_path)
        write_report(data_dir, date, slot, text, label="本周回顾与总结" if plan.get("mode") == "weekly" else None)
        _log("sent slot=%s mode=%s ids=%s" % (slot, plan["mode"], ids), data_dir)
        print("已发送 %s %s（%d 字）" % (date, SLOT_LABELS[slot], len(text)))
        return EXIT_OK

    # 安全默认：手动跑一轮只落报告、不推送、不推进状态。
    write_report(data_dir, date, slot, text, label="本周回顾与总结" if plan.get("mode") == "weekly" else None)
    print(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
