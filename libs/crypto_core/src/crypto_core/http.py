# -*- coding: utf-8 -*-
"""统一 HTTP 客户端（urllib，零第三方依赖）。

- 默认浏览器 UA
- 补 Python 3.9 urllib 不跟随 308
- 统一超时 / 可选重试与退避
- POST JSON / JSON-RPC（公共 RPC、OpenAI 兼容接口共用）
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA_DEFAULT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9 的 urllib 不自动跟随 308，这里补上（只对 GET/HEAD；POST 遇 308 照常抛 HTTPError，
    避免把请求体丢掉后悄悄改成 GET 重发）。"""

    def http_error_308(self, req, fp, code, msg, headers):
        newurl = headers.get("Location")
        if newurl and req.get_method() in ("GET", "HEAD"):
            newurl = urllib.parse.urljoin(req.full_url, newurl)
            return self.parent.open(urllib.request.Request(newurl, headers=dict(req.headers)), timeout=req.timeout)
        return None


_OPENER = urllib.request.build_opener(_Redirect308())


def request(url, data=None, timeout=15, headers=None, ua=None, retries=0, backoff=1.0):
    """发请求并返回原始 bytes（data=None 为 GET，否则 POST）；retries>0 时失败按指数退避重试。"""
    h = {"User-Agent": ua or UA_DEFAULT, "Accept": "*/*"}
    if headers:
        h.update({k: v for k, v in headers.items() if isinstance(v, str)})
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with _OPENER.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - 网络错误统一重试
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    raise last_err


def get(url, timeout=15, headers=None, ua=None, retries=0, backoff=1.0):
    """GET 一个 URL 并返回原始 bytes；retries>0 时失败重试。"""
    return request(url, timeout=timeout, headers=headers, ua=ua, retries=retries, backoff=backoff)


def post_json(url, payload, timeout=15, headers=None, ua=None, retries=0, backoff=1.0):
    """POST JSON（payload 可为 dict 或 list）并把响应解析为 JSON。"""
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw = request(url, data=body, timeout=timeout, headers=h, ua=ua, retries=retries, backoff=backoff)
    return json.loads(raw.decode("utf-8", "ignore"))


def json_rpc(url, method, params, timeout=25, ua="crypto-research/1.0"):
    """单次 JSON-RPC 2.0 调用，返回 result；响应不是对象抛 ValueError，带 error 抛 RuntimeError。"""
    data = post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                     timeout=timeout, ua=ua)
    if not isinstance(data, dict):
        raise ValueError("rpc 响应不是对象")
    if data.get("error"):
        raise RuntimeError("rpc error: %s" % str(data["error"])[:160])
    return data.get("result")


def get_bytes(url, **kwargs):
    """取原始字节（用于 xlsx 等二进制资源）。"""
    return get(url, **kwargs)


def get_text(url, **kwargs):
    return get(url, **kwargs).decode("utf-8", "ignore")


def get_json(url, **kwargs):
    return json.loads(get_text(url, **kwargs))
