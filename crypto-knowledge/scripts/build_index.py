#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_index.py —— 从课程内容库生成可读目录 docs/crypto-knowledge/course-index.md。

只读 config/curriculum/stage*.yaml，不联网、不推送、不改内容。
用法：
    python3 scripts/build_index.py            # 生成/覆盖目录
    python3 scripts/build_index.py --check    # 校验目录是否最新（CI/测试用）
"""
import argparse
import os
import sys

import content

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "..", "docs", "crypto-knowledge", "course-index.md")
TYPE_TITLE = {"concept": "概念课", "theory": "理论课", "practice": "实践课"}
TYPE_ORDER = ("concept", "theory", "practice")


def _gist(item, limit=56):
    sections = item.get("sections") or []
    text = str((sections[0] or {}).get("p") or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _cell(text):
    """Markdown 表格里不能有裸 | 和换行。"""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def build(items):
    lines = [
        "# 课程目录（160 条 · 10–15 分钟/条）",
        "",
        "> 由 [`scripts/build_index.py`](../../crypto-knowledge/scripts/build_index.py) 从",
        "> [`config/curriculum/`](../../crypto-knowledge/config/curriculum/README.md) 自动生成；不要手改，改内容后重新生成。",
        "",
        "阅读顺序：阶段 1 → 4；每天 13:00 概念课、19:00 理论/实践课、21:00 回顾总结（周日为本周回顾）、23:00 复盘课。",
        "",
    ]

    # 总览
    lines.extend(["## 总览", "", "| 阶段 | 主题 | 概念 | 理论 | 实践 | 合计 |", "|---|---|---|---|---|---|"])
    inv = content.inventory(items)
    for stage in sorted(inv):
        bucket = inv[stage]
        sample = next(it for it in items if int(it.get("stage") or 0) == stage)
        lines.append("| %d | %s | %d | %d | %d | %d |"
                     % (stage, _cell(sample.get("_stage_title") or ""),
                        bucket["concept"], bucket["theory"], bucket["practice"], bucket["total"]))
    lines.append("")

    for stage in sorted(inv):
        stage_items = [it for it in items if int(it.get("stage") or 0) == stage]
        if not stage_items:
            continue
        sample = stage_items[0]
        title = sample.get("_stage_title") or ""
        weeks = sample.get("_weeks") or ""
        lines.extend(["## 阶段 %d · %s%s" % (stage, title, ("（%s）" % weeks) if weeks else ""), ""])
        for item_type in TYPE_ORDER:
            group = [it for it in stage_items if it.get("type") == item_type]
            if not group:
                continue
            lines.extend(["### %s（%d 条）" % (TYPE_TITLE[item_type], len(group)), "",
                          "| 编号 | 标题 | 英文 | 分钟 | 自测 | 一句话 |",
                          "|---|---|---|---|---|---|"])
            for item in group:
                quiz_count = len(content.quiz_items(item))
                lines.append("| `%s` | %s | %s | %d | %d 题 | %s |"
                             % (item.get("id"), _cell(item.get("title")),
                                _cell(item.get("en") or ""), int(item.get("minutes") or 0),
                                quiz_count, _cell(_gist(item))))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 crypto-knowledge 课程目录")
    parser.add_argument("--check", action="store_true", help="只校验目录是否最新")
    parser.add_argument("--output", default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    items = content.load_all()
    content.validate_or_raise(items)
    text = build(items)
    output = os.path.abspath(args.output)

    if args.check:
        try:
            current = open(output, encoding="utf-8").read()
        except OSError:
            print("目录不存在：%s（先运行 build_index.py）" % output)
            return 1
        if current != text:
            print("目录不是最新：%s（内容已变更，重新运行 build_index.py）" % output)
            return 1
        print("目录最新 ✅（%s）" % output)
        return 0

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)
    print("已生成：%s（%d 条）" % (output, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
