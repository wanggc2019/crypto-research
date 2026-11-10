# -*- coding: utf-8 -*-
"""统一 HTTP 客户端（urllib，零第三方依赖）。

- 默认浏览器 UA
- 补 Python 3.9 urllib 不跟随 308
- 统一超时 / 可选重试与退避
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
    """Python 3.9 的 urllib 不自动跟随 308，这里补上。"""

    def http_error_308(self, req, fp, code, msg, headers):
        newurl = headers.get("Location")
        if newurl:
            newurl = urllib.parse.urljoin(req.full_url, newurl)
            return self.parent.open(urllib.request.Request(newurl, headers=dict(req.headers)), timeout=req.timeout)
        return None


_OPENER = urllib.request.build_opener(_Redirect308())


def get(url, timeout=15, headers=None, ua=None, retries=0, backoff=1.0):
    """GET 一个 URL 并返回原始 bytes；retries>0 时失败重试。"""
    h = {"User-Agent": ua or UA_DEFAULT, "Accept": "*/*"}
    if headers:
        h.update({k: v for k, v in headers.items() if isinstance(v, str)})
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with _OPENER.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - 网络错误统一重试
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    raise last_err


def get_bytes(url, **kwargs):
    """取原始字节（用于 xlsx 等二进制资源）。"""
    return get(url, **kwargs)


def get_text(url, **kwargs):
    return get(url, **kwargs).decode("utf-8", "ignore")


def get_json(url, **kwargs):
    return json.loads(get_text(url, **kwargs))
