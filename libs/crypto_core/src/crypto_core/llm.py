# -*- coding: utf-8 -*-
"""OpenAI 兼容 /chat/completions 客户端（默认 DeepSeek），零第三方依赖。

只负责「找 key → 发请求 → 取文本 → 抽 JSON」；提示词、批量、缓存、失败降级策略留在调用方。
注意：调用即把内容发给第三方 API（推文/标题/政策原文/代码 diff），调用方需自行确认可外发。
"""
import json
import os
import re
import urllib.error

from . import http

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DSH_CREDENTIALS = "~/.dsh/.credentials.yaml"


class LLMError(RuntimeError):
    """请求/HTTP/响应结构错误（消息含 HTTP 状态码与响应片段，不含 key）。"""


def _dsh_ref(key_name, path=None):
    """从 ~/.dsh/.credentials.yaml 的 refs 读 key（有 PyYAML 用 YAML，否则按行匹配）；读不到返回 ""。"""
    path = os.path.expanduser(path or DSH_CREDENTIALS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        refs = data.get("refs") if isinstance(data, dict) else None
        if isinstance(refs, dict) and refs.get(key_name):
            return str(refs[key_name]).strip()
    except Exception:  # noqa: BLE001 - YAML 缺失/损坏时退回按行匹配
        pass
    m = re.search(r"%s:\s*[\"']?([^\s\"']+)" % re.escape(key_name), text)
    return m.group(1).strip() if m else ""


def find_api_key(env=None, names=("DEEPSEEK_API_KEY",), use_os_environ=True, dsh_fallback=True):
    """按顺序找 API key：env dict（如 .env）→ 进程环境变量 → ~/.dsh 凭据 refs；都没有返回 ""。"""
    sources = []
    if env is not None:
        sources.append(env)
    if use_os_environ:
        sources.append(os.environ)
    for src in sources:
        for name in names:
            value = str(src.get(name) or "").strip()
            if value:
                return value
    if dsh_fallback:
        for name in names:
            value = _dsh_ref(name)
            if value:
                return value
    return ""


def chat(messages, api_key, model=None, base_url=None, timeout=60, retries=0, **params):
    """调一次 chat/completions，返回 choices[0].message.content（str）。

    params 原样并入请求体（temperature / max_tokens / response_format ...）；
    retries>0 时失败立即重试（不退避，与原各模块实现一致）。失败抛 LLMError。
    """
    if not api_key:
        raise LLMError("missing API key")
    payload = {"model": model or DEFAULT_MODEL, "messages": messages}
    payload.update({k: v for k, v in params.items() if v is not None})
    url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    last = None
    for _ in range(int(retries or 0) + 1):
        try:
            data = http.post_json(url, payload, timeout=timeout,
                                  headers={"Authorization": "Bearer " + api_key})
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            finally:
                e.close()
            last = LLMError("HTTP %s: %s" % (e.code, detail))
        except urllib.error.URLError as e:
            last = LLMError("网络错误：%s" % e)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            last = LLMError("响应格式不对：%s: %s" % (type(e).__name__, e))
        except OSError as e:  # socket.timeout 等
            last = LLMError("%s: %s" % (type(e).__name__, e))
    raise last


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
