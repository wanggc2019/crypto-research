#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py —— Telegram 推送封装（周报 / 日报 / 测试）。

凭据只读仓库根 .env：
- 优先 TELEGRAM_JOBS_BOT_TOKEN / TELEGRAM_JOBS_CHAT_ID（本模块专用 Bot，如 独立 Bot）
- 缺省回退 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（共享 Bot）
dry-run 只打印，不发网络请求。
"""
import argparse
import os
import sys

import report

from common import job_name, load_bot_env
from crypto_core.notify import check_credentials, send_document, send_message, telegram


def send(text, dry_run=False, silent=False):
    """发送 HTML 文本，自动补 🏷 任务标签；返回 (ok, error)。"""
    if "🏷" not in text:
        text = text.rstrip() + "\n\n🏷 任务：%s" % job_name()
    if dry_run:
        print(text)
        return True, ""
    token, chat, problems = check_credentials(load_bot_env())
    if problems:
        return False, "凭据不完整：%s" % "；".join(problems)
    return send_message(token, chat, text, silent=silent)


def send_weekly(data, dry_run=False, report_path=None):
    """周报送达：Telegram 摘要（引导阅读）+ 完整 Markdown 报告附件（sendDocument）。"""
    ok, err = send(report.render_telegram(data), dry_run=dry_run)
    if not ok or dry_run:
        return ok, err
    if not report_path or not os.path.isfile(report_path):
        return True, ""
    token, chat, problems = check_credentials(load_bot_env())
    if problems:
        return False, "摘要已发出，但文档凭据不完整：%s" % "；".join(problems)
    overview = data.get("overview") or {}
    caption = "📄 完整周报 · %s（%d 个岗位 / %d 家公司）" % (
        data.get("date"), overview.get("jobs", 0), overview.get("companies", 0))
    ok_file, err_file = send_document(token, chat, report_path, caption=caption, silent=True)
    if not ok_file:
        return False, "摘要已发出，完整报告上传失败：%s" % err_file
    return True, ""


def whoami(dry_run=False):
    """返回当前报告 Bot 的 @username，用于确认到底发到哪个 Bot。"""
    token, chat, problems = check_credentials(load_bot_env())
    if problems:
        return "", "；".join(problems)
    if dry_run:
        return "(dry-run)", ""
    try:
        res = telegram.tg_call(token, "getMe", {})
        user = res.get("result") or {}
        return user.get("username") or user.get("first_name") or "", ""
    except Exception as exc:  # noqa: BLE001 - 查询失败不影响发送
        return "", "%s: %s" % (type(exc).__name__, exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-jobs Telegram 推送")
    parser.add_argument("--test", action="store_true", help="发送一条测试卡片")
    parser.add_argument("--whoami", action="store_true", help="查看当前使用的 Bot 用户名（不发送消息）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    args = parser.parse_args(argv)

    if args.whoami:
        username, err = whoami(dry_run=args.dry_run)
        if err:
            print("❌ %s" % err)
            return 2
        print("Bot: @%s" % username)
        return 0
    if args.test:
        ok, err = send(report.render_test_card(), dry_run=args.dry_run, silent=True)
        if ok:
            username, _err = whoami()
            print("✅ 测试卡片已发送%s" % ("（Bot: @%s）" % username if username else ""))
            return 0
        print("❌ 发送失败：%s" % err)
        return 2
    print("用法：notify.py --test [--dry-run]；日常推送由 scripts/main.py 负责")
    return 0


if __name__ == "__main__":
    sys.exit(main())
