#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_text.py —— 把一段纯文本发到 Telegram（带模块标识与 🏷 任务标签）。

用法：
    python3 scripts/send_text.py data/reports/2026-W45-ai-summary.txt
    echo "测试" | python3 scripts/send_text.py

规则：
  - 正文做 HTML 转义（Telegram parse_mode=HTML），防止出现 400 解析错误；
  - 自动带上 🏷 任务：<Label>（launchd 注入 CRYPTO_JOB_LABEL；手动运行回退「（手动运行）」）；
  - 只读文件、只发送，不写任何状态。
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "libs", "crypto_core", "src"))

MAX_LEN = 4096


def job_label(module="crypto-analyst.ai-analysis"):
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if job.startswith("com.crypto-research"):
        return job
    return "%s（手动运行）" % module


def build(text, label=None):
    body = html.escape((text or "").strip(), quote=False)
    tail = "⚠️ 研究/方法验证，不构成投资建议\n🏷 任务：%s" % (label or job_label())
    if "🏷 任务：" not in body:
        body += "\n\n" + tail
    return body[:MAX_LEN]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        with open(argv[0], encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("没有要发送的内容", file=sys.stderr)
        return 2
    msg = build(text)
    from crypto_core import env as envlib
    from crypto_core.notify import check_credentials, send_message
    token, chat, problems = check_credentials(envlib.load_env())
    if problems:
        print("Telegram 凭据不完整：%s" % "；".join(problems), file=sys.stderr)
        return 2
    ok, err = send_message(token, chat, msg)
    if not ok:
        print("发送失败：%s" % err, file=sys.stderr)
        return 1
    print("已发送（%d 字符）" % len(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
