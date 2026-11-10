#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py —— Telegram 推送封装（速报 / 测试）。

用法：
    python3 scripts/notify.py --test        # 发一条测试卡片（会真的发送）
    python3 scripts/notify.py --test --dry-run
"""
import argparse
import sys

from brief import sample_card

from common import job_name, load_env
from crypto_core.notify import check_credentials, send_message


def send(text, dry_run=False, silent=False):
    """发送 HTML 文本；返回 (ok, error)。dry_run 只打印。

    统一在这里补 🏷 任务标签，避免各个卡片忘记带、分不清来源。
    """
    if "🏷" not in text:
        text = text.rstrip() + "\n\n🏷 任务：%s" % job_name()
    if dry_run:
        print(text)
        return True, ""
    token, chat, problems = check_credentials(load_env())
    if problems:
        return False, "凭据不完整：%s" % "；".join(problems)
    ok, err = send_message(token, chat, text, silent=silent)
    return ok, err


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-policy Telegram 推送")
    parser.add_argument("--test", action="store_true", help="发送一条测试卡片")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    args = parser.parse_args(argv)

    if args.test:
        ok, err = send(sample_card(), dry_run=args.dry_run)
        if ok:
            print("✅ 测试卡片已发送" if not args.dry_run else "✅ dry-run 通过")
            return 0
        print("❌ 发送失败：%s" % err)
        return 2

    print("用法：--test [--dry-run]；日常推送由 scripts/main.py 负责")
    return 0


if __name__ == "__main__":
    sys.exit(main())
