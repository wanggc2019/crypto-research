#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze.py —— 用 DeepSeek 把外文 KOL 推文翻成中文，并给每条一句话总结。

设计：
- 只在 digest 运行时批量调用（默认每批 8 条），结果缓存到 data/state/analysis-<date>.json，
  同一推文不重复付费；报告/告警只展示中文译文 + 一句话总结（原文留在 raw 里备查，不再展示）。
- 中文推文不翻译（原文本就是中文），但仍生成一句话总结。
- 没有 DEEPSEEK_API_KEY 时**优雅降级**：不报错，报告/告警回退显示原文。
  凭据优先级：.env 的 DEEPSEEK_API_KEY → ~/.dsh/.credentials.yaml 的 refs（与 crypto-news inspect 一致）。
- 网络调用集中在 call_deepseek()，测试可注入 caller 做离线回放。

用法（一般不单独跑，digest 内部调用）：
    python3 scripts/analyze.py --fixtures ...   # 预留；当前仅暴露纯函数供测试
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config, load_env, ts_iso  # noqa: E402

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DEFAULT_ANALYSIS = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "batch_size": 8,
    "timeout_seconds": 60,
    "retries": 1,
    "max_tweet_chars": 1500,
}

SYSTEM_PROMPT = """你是加密市场情报分析员，负责把 KOL 推文整理成中文情报。要求：
1. 对非中文推文给出忠实、简洁的中文翻译（不要添加原文没有的信息，不要漏掉关键数字/资产/时间）。
2. 对每条推文用一句中文总结「他说了什么」：事实/观点/操作/预测，20–60 字；不要评价、不要投资建议、不要复述翻译。
3. 保持 id 不变；只输出 JSON，不要 Markdown 代码块。格式：
{"items":[{"id":"<原 id>","lang":"en|zh|other","zh":"<中文翻译；中文原文可留空>","summary":"<一句话总结>"}]}"""


def analysis_config(cfg):
    """从 app.yaml 的 analysis 段读配置，合并默认值。"""
    raw = (cfg or {}).get("analysis") or {}
    merged = dict(DEFAULT_ANALYSIS)
    merged.update(raw)
    return merged


def load_api_key(cfg, env=None):
    """读 DeepSeek key：先 .env/环境变量，再 ~/.dsh/.credentials.yaml 的 refs。"""
    env = env if env is not None else load_env()
    key = (env.get(cfg.get("api_key_env") or "DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    path = os.path.expanduser("~/.dsh/.credentials.yaml")
    if os.path.isfile(path):
        try:
            import yaml

            data = yaml.safe_load(open(path, "r", encoding="utf-8")) or {}
            return str(((data.get("refs") or {}).get(cfg.get("api_key_env") or "DEEPSEEK_API_KEY")) or "").strip()
        except (OSError, ValueError, ImportError):
            return ""
    return ""


def is_chinese(text, ratio=0.3):
    """粗判中文：CJK 字符占比 ≥30%（混合中英也算中文，不需要翻译）。"""
    text = str(text or "")
    cjk = len(CJK_RE.findall(text))
    if cjk < 2:
        return False
    return cjk >= int(len(text) * ratio)


def _tweet_payload(posts, cfg):
    limit = int(cfg.get("max_tweet_chars") or 1500)
    items = []
    for post in posts:
        text = " ".join(str(post.get("text") or "").split())
        if len(text) > limit:
            text = text[:limit] + "…"
        quote = " ".join(str(post.get("quote") or "").split())
        if len(quote) > limit // 2:
            quote = quote[: limit // 2] + "…"
        items.append({"id": post.get("id"), "handle": post.get("handle"), "text": text, "quote": quote})
    return items


def build_request(posts, cfg):
    """构造 OpenAI 兼容的 /chat/completions 请求体（纯函数，便于测试）。"""
    user = json.dumps({"tweets": _tweet_payload(posts, cfg)}, ensure_ascii=False)
    return {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }


def parse_response(content):
    """解析模型返回的 JSON（容忍代码块/前后废话）；返回 {id: {lang, zh, summary}}。"""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出不是 JSON 对象")
    data = json.loads(text[start: end + 1])
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("模型输出缺少 items 数组")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out[str(row["id"])] = {
            "lang": str(row.get("lang") or ""),
            "zh": " ".join(str(row.get("zh") or "").split()),
            "summary": " ".join(str(row.get("summary") or "").split()),
        }
    return out


def call_deepseek(posts, cfg, api_key):
    """真实网络调用；失败抛异常（由 analyze 捕获并降级）。"""
    url = (cfg.get("base_url") or DEFAULT_ANALYSIS["base_url"]).rstrip("/") + "/chat/completions"
    body = json.dumps(build_request(posts, cfg), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    last_err = None
    for attempt in range(int(cfg.get("retries") or 0) + 1):
        try:
            with urllib.request.urlopen(req, timeout=float(cfg.get("timeout_seconds") or 60)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return parse_response(data["choices"][0]["message"]["content"])
        except Exception as exc:  # noqa: BLE001 - 统一记录后重试
            last_err = exc
    raise last_err


def analyze(posts, cfg_in=None, env=None, cache=None, caller=None, log=print):
    """给 posts 补中文翻译/总结；返回 cache（不修改 posts）。

    caller 可注入（测试用）：caller(batch, cfg, api_key) -> {id: {lang, zh, summary}}。
    """
    cfg = dict(DEFAULT_ANALYSIS)
    cfg.update(cfg_in or {})
    cache = cache if cache is not None else {}
    if not cfg.get("enabled", True):
        return cache
    todo = [p for p in posts if p.get("id") and str(p["id"]) not in cache]
    if not todo:
        return cache
    key = load_api_key(cfg, env)
    if not key and caller is None:
        log("  [analyze] 没有 DEEPSEEK_API_KEY，跳过翻译/总结（报告回退原文）")
        return cache
    caller = caller or call_deepseek
    batch_size = max(1, int(cfg.get("batch_size") or 8))
    stamp = ts_iso()
    for start in range(0, len(todo), batch_size):
        batch = todo[start: start + batch_size]
        try:
            results = caller(batch, cfg, key)
        except Exception as exc:  # noqa: BLE001 - 单批失败不拖垮整轮
            log("  [analyze] 批次失败（%d 条）：%s" % (len(batch), str(exc)[:120]))
            continue
        for post in batch:
            row = results.get(str(post.get("id")))
            if not row or (not row.get("zh") and not row.get("summary")):
                continue
            cache[str(post["id"])] = {
                "lang": row.get("lang") or "",
                "zh": row.get("zh") or "",
                "summary": row.get("summary") or "",
                "ts": stamp,
                "model": cfg.get("model") or "",
            }
    return cache


def localize(post, cache):
    """返回 (展示文本, 一句话总结, lang, 是否翻译)；无缓存时回退原文、translated=False。"""
    item = (cache or {}).get(str(post.get("id"))) or {}
    zh = item.get("zh") or ""
    text = zh or (post.get("text") or "")
    summary = item.get("summary") or ""
    lang = item.get("lang") or ("zh" if is_chinese(post.get("text")) else "")
    translated = bool(zh) and lang != "zh"
    return text, summary, lang, translated


def main(argv=None):  # pragma: no cover - 预留 CLI，方便手工验证单条
    import argparse

    parser = argparse.ArgumentParser(description="分析单条推文（手工验证用）")
    parser.add_argument("text", help="推文正文")
    parser.add_argument("--handle", default="test")
    parser.add_argument("--dry-run", action="store_true", help="只打印请求，不调用 API")
    args = parser.parse_args(argv)
    post = {"id": "manual-1", "handle": args.handle, "text": args.text, "quote": ""}
    cfg = analysis_config(load_config("app.yaml") or {})
    if args.dry_run:
        print(json.dumps(build_request([post], cfg), ensure_ascii=False, indent=1))
        return 0
    cache = analyze([post], cfg, env=load_env())
    print(json.dumps(cache.get("manual-1") or {}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
