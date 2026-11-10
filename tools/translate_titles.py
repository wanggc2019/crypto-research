#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_titles.py —— 把英文新闻标题批量翻译成自然中文（DeepSeek API，零第三方依赖）。

用法：
    echo '["title a","title b"]' | python3 tools/translate_titles.py
输出：
    {"translations": ["译文 a", "译文 b"]}

凭据：环境变量 DEEPSEEK_API_KEY；没有则从 ~/.dsh/.credentials.yaml 的 refs 读取（与 headless 一致）。
失败时退出码 1 并输出 {"error": "..."}，调用方应回退到规则翻译。
"""
import argparse
import json
import os
import re
import sys
import urllib.request

SYSTEM_PROMPT = (
    "你是中文财经/加密新闻编辑。把每条英文标题改写成自然、准确、信息完整的中文一句话，"
    "不要逐词直译：讲清谁做了什么、结果/影响是什么。机构/代币名用中文常用译名"
    "（Binance=币安、Bitcoin=比特币、Ethereum=以太坊、Nvidia=英伟达、Elliptic=Elliptic），数字保留；"
    "不要解释。输出与输入等长的 JSON 字符串数组，只输出 JSON。"
)


def find_api_key():
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    path = os.path.expanduser("~/.dsh/.credentials.yaml")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    m = re.search(r"DEEPSEEK_API_KEY:\s*[\"']?([^\s\"']+)", text)
    return m.group(1).strip() if m else ""


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
    key = find_api_key()
    if not key:
        return {"error": "missing DEEPSEEK_API_KEY"}
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(titles, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = parse_translations(data["choices"][0]["message"]["content"], len(titles))
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
