#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_titles.py —— 把英文新闻标题批量翻译成自然中文（用哪个 AI 见 config/ai.yaml 的 news.translate）。

用法：
    echo '["title a","title b"]' | python3 tools/translate_titles.py
输出：
    {"translations": ["译文 a", "译文 b"]}

凭据：默认用本机 Claude Code 订阅（claude -p）；改用 anthropic/deepseek 时读对应 key（环境变量优先，否则仓库根 .env）。
失败时退出码 1 并输出 {"error": "..."}，调用方应回退到规则翻译。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libs", "crypto_core", "src"))
from crypto_core import llm  # noqa: E402

SYSTEM_PROMPT = (
    "你是中文财经/加密新闻编辑。把每条英文标题改写成自然、准确、信息完整的中文一句话，"
    "不要逐词直译：讲清谁做了什么、结果/影响是什么。机构/代币名用中文常用译名"
    "（Binance=币安、Bitcoin=比特币、Ethereum=以太坊、Nvidia=英伟达、Elliptic=Elliptic），数字保留；"
    "不要解释。输出与输入等长的 JSON 字符串数组，只输出 JSON。"
)


TASK = "news.translate"


def find_api_key(spec):
    """环境变量 → 仓库根 .env（见 crypto_core.llm.key_for）。"""
    return llm.key_for(spec)


def parse_translations(content, expected):
    """解析模型输出：兼容 ```json 包裹/前后空白；长度不符返回 None。"""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    return [str(x).strip() for x in data]


def translate(titles, timeout=45):
    try:
        spec = llm.resolve(TASK)
    except llm.LLMError as e:
        return {"error": str(e)}
    key = find_api_key(spec)
    if not key:
        return {"error": "missing %s" % (spec["key_env"] or "claude 命令（Claude Code 订阅）")}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(titles, ensure_ascii=False)},
    ]
    try:
        out = parse_translations(llm.complete(spec, messages, key, timeout=timeout), len(titles))
        if out is None:
            return {"error": "unparseable model output"}
        return {"translations": out}
    except Exception as e:  # noqa: BLE001 - 网络/解析错误统一返回给调用方回退
        return {"error": "%s: %s" % (type(e).__name__, e)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量翻译英文标题")
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args(argv)
    try:
        titles = json.load(sys.stdin)
    except ValueError:
        print(json.dumps({"error": "stdin 必须是 JSON 字符串数组"}, ensure_ascii=False))
        return 2
    if not isinstance(titles, list) or not all(isinstance(x, str) for x in titles):
        print(json.dumps({"error": "stdin 必须是 JSON 字符串数组"}, ensure_ascii=False))
        return 2
    result = translate(titles, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if "translations" in result else 1


if __name__ == "__main__":
    sys.exit(main())
