# -*- coding: utf-8 -*-
"""Claude（Anthropic Messages API）客户端：全仓唯一的 LLM 入口（ADR 0011）。

只负责「找 key → 发请求 → 取文本 → 抽 JSON」；提示词、批量、缓存、失败降级策略留在调用方。
注意：调用即把内容发给 Anthropic（推文/标题/政策原文/代码 diff），调用方需自行确认可外发。

模型按任务选（调用方传 model；不传用 DEFAULT_MODEL）：
- FAST_MODEL（Haiku）：高频、简单任务（推文翻译/总结、标题翻译），配 effort="low"；
- DEFAULT_MODEL（Opus）：需要判断力的任务（政策解读、PR 审查）。
"""
import json
import os
import re

from . import env as _env

DEFAULT_MODEL = "claude-opus-5-5"
FAST_MODEL = "claude-haiku-5-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MAX_TOKENS = 16000  # 含思考 token；非流式请求的推荐上限，太小会截断
# 服务端拒答回退（stop_reason=refusal 时由 API 在同一次调用里换模型重跑）；Haiku 不支持
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5")


class LLMError(RuntimeError):
    """请求/HTTP/响应错误（消息含 HTTP 状态码与片段，不含 key）。"""


def find_api_key(env=None, names=(API_KEY_ENV,), use_os_environ=True, use_env_file=True):
    """按顺序找 API key：env dict → 进程环境变量 → 仓库根 .env；都没有返回 ""。"""
    sources = []
    if env is not None:
        sources.append(env)
    if use_os_environ:
        sources.append(os.environ)
    if use_env_file:
        sources.append(_env.load_env())
    for src in sources:
        for name in names:
            value = str(src.get(name) or "").strip()
            if value:
                return value
    return ""


def _split_system(messages):
    """OpenAI 风格的 [{"role": "system"}, ...] → (system 文本, 其余 messages)。"""
    system, rest = [], []
    for m in messages or []:
        if m.get("role") == "system":
            system.append(str(m.get("content") or ""))
        else:
            rest.append({"role": m["role"], "content": m["content"]})
    return "\n\n".join(s for s in system if s), rest


def _text_of(resp):
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise LLMError("模型拒绝回答（%s）" % (getattr(details, "category", None) or "未说明类别"))
    if resp.stop_reason == "max_tokens":
        raise LLMError("输出被 max_tokens 截断")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise LLMError("响应里没有文本（stop_reason=%s）" % resp.stop_reason)
    return text


def chat(messages, api_key, model=None, max_tokens=DEFAULT_MAX_TOKENS, effort=None,
         timeout=60, retries=2, base_url=None):
    """调一次 Messages API，返回回答文本（str）。失败抛 LLMError。

    messages 可含 role=system（自动并入 system 参数）；effort: low|medium|high|xhigh|max（None=模型默认）；
    retries 交给 SDK（429/5xx/网络错误指数退避重试）；base_url 只给离线测试用。
    """
    if not api_key:
        raise LLMError("missing API key")
    import anthropic  # 延迟导入：不调 LLM 的模块不需要 SDK

    model = model or DEFAULT_MODEL
    system, msgs = _split_system(messages)
    kwargs = {"model": model, "max_tokens": int(max_tokens), "messages": msgs}
    if system:
        kwargs["system"] = system
    if effort:
        kwargs["output_config"] = {"effort": effort}
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout,
                                 max_retries=int(retries or 0))
    try:
        if model in FALLBACK_MODELS:
            resp = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise LLMError("HTTP %s: %s" % (e.status_code, str(e.message)[:300])) from None
    except anthropic.APIConnectionError as e:
        raise LLMError("网络错误：%s" % e) from None
    return _text_of(resp)


def extract_json(text, kind="object"):
    """从模型输出里取 JSON（容忍 ```json 围栏与前后废话）。kind: "object" | "array"。解析失败抛 ValueError。"""
    t = str(text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    open_ch, close_ch = ("{", "}") if kind == "object" else ("[", "]")
    start, end = t.find(open_ch), t.rfind(close_ch)
    if start < 0 or end <= start:
        raise ValueError("模型输出不是 JSON %s" % kind)
    return json.loads(t[start: end + 1])
