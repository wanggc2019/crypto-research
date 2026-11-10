#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_content.py —— 课程内容校验 CLI（离线，不联网、不写盘）。

用法：
    python3 crypto-knowledge/scripts/check_content.py
    python3 crypto-knowledge/scripts/check_content.py --json
    python3 crypto-knowledge/scripts/check_content.py --content tests/fixtures/curriculum
"""
import argparse
import json
import sys

import content


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验 crypto-knowledge 课程内容")
    parser.add_argument("--content", default=None, help="内容目录（默认 config/curriculum）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    items = content.load_all(args.content)
    problems = content.validate(items)
    inv = content.inventory(items)

    if args.json:
        print(json.dumps({"count": len(items), "inventory": inv, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        print("条目总数：%d" % len(items))
        for stage in sorted(inv):
            b = inv[stage]
            print("  阶段 %s：概念 %d / 理论 %d / 实践 %d（共 %d）"
                  % (stage, b["concept"], b["theory"], b["practice"], b["total"]))
        if problems:
            print("发现问题 %d 项：" % len(problems))
            for line in problems[:50]:
                print("  - " + line)
        else:
            print("校验通过 ✅")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
