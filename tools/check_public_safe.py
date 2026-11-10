#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_public_safe.py —— 公开仓库信息检查（gate 步骤）。

扫描 git 跟踪的文件，禁止：
- macOS 绝对路径 `/Users/<name>/`（暴露本机用户名）
- Telegram chat id / bot token 形式的字面值
- 常见密钥前缀（ghp_/sk-/xoxb- 等，长随机串）
允许：仓库 URL（本仓库）等公开标识。
"""
import re
import subprocess
import sys

PATTERNS = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "macOS 绝对路径"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "疑似 Telegram bot token"),
    (re.compile(r"TELEGRAM_[A-Z_]*CHAT_ID\s*=\s*\d{6,}"), "真实 Telegram chat_id"),
    (re.compile(r"\b(ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI/DeepSeek 风格 key"),
    # 个人信息/位置/内部称呼（当前仓库不应出现；checker 自身除外）
    (re.compile(r"Asia/Dubai|UTC\+4|迪拜"), "位置/时区信息"),
    (re.compile(r"中国公民"), "个人身份信息"),
    (re.compile(r"\bBoss\b|老板"), "内部称呼"),
    (re.compile(r"wanggc|Wgc[A-Za-z]"), "个人账号/机器人名"),
]


def main():
    try:
        files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
    except (OSError, subprocess.CalledProcessError) as exc:
        print("无法读取 git 文件列表：%s" % exc)
        return 2
    bad = []
    for path in files:
        if not path or path.startswith("data/") or path == "tools/check_public_safe.py":
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for pattern, label in PATTERNS:
                        if pattern.search(line):
                            bad.append("%s:%s: %s" % (path, lineno, label))
        except OSError:
            continue
    if bad:
        print("发现 %d 处可能暴露个人信息/密钥的内容：" % len(bad))
        for item in bad[:30]:
            print("  " + item)
        return 1
    print("公开仓库信息检查通过（无绝对路径 / token / chat_id）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
