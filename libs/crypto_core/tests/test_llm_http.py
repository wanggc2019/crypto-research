# -*- coding: utf-8 -*-
"""crypto_core.http（POST/JSON-RPC/308）与 crypto_core.llm：本地 http.server，不联网。"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from crypto_core import http, llm


class _Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.calls.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/redirect":
            return self._send(308, {}, {"Location": "/ok"})
        self._send(200, {"path": self.path})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null")
        self.calls.append(("POST", self.path, dict(self.headers), body))
        if self.path == "/redirect":
            return self._send(308, {}, {"Location": "/ok"})
        if self.path == "/rpc":
            if body.get("method") == "bad":
                return self._send(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}})
            return self._send(200, {"jsonrpc": "2.0", "id": 1, "result": "0x10"})
        if self.path == "/batch":
            return self._send(200, [{"id": i, "result": i} for i, _ in enumerate(body)])
        if self.path.endswith("/chat/completions"):
            if self.headers.get("Authorization") == "Bearer bad":
                return self._send(401, {"error": "invalid key"})
            if self.headers.get("Authorization") == "Bearer shape":
                return self._send(200, {"choices": []})
            return self._send(200, {"choices": [{"message": {"content": "hi " + body["model"]}}]})
        self._send(404, {})


class HttpLlmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.calls.clear()

    # ---------------- http
    def test_get_follows_308(self):
        self.assertEqual(http.get_json(self.base + "/redirect"), {"path": "/ok"})

    def test_post_does_not_follow_308(self):
        with self.assertRaises(http.urllib.error.HTTPError) as ctx:
            http.post_json(self.base + "/redirect", {"a": 1})
        self.assertEqual(ctx.exception.code, 308)
        self.assertEqual([c[0] for c in _Handler.calls], ["POST"], "不能把 POST 改成 GET 重发")

    def test_post_json_list_and_headers(self):
        out = http.post_json(self.base + "/batch", [{"x": 1}, {"x": 2}], ua="ua-test/1")
        self.assertEqual(out, [{"id": 0, "result": 0}, {"id": 1, "result": 1}])
        headers = _Handler.calls[-1][2]
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(headers.get("User-Agent"), "ua-test/1")

    def test_negative_retries_still_tries_once(self):
        self.assertEqual(http.get_json(self.base + "/x", retries=-1), {"path": "/x"})
        self.assertEqual(len(_Handler.calls), 1)

    def test_json_rpc(self):
        self.assertEqual(http.json_rpc(self.base + "/rpc", "eth_blockNumber", []), "0x10")
        body = _Handler.calls[-1][3]
        self.assertEqual(body, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
        self.assertEqual(_Handler.calls[-1][2].get("User-Agent"), "crypto-research/1.0")
        with self.assertRaisesRegex(RuntimeError, "rpc error"):
            http.json_rpc(self.base + "/rpc", "bad", [])

    # ---------------- llm
    def test_chat_ok_with_params(self):
        out = llm.chat([{"role": "user", "content": "x"}], "k", model="m1", base_url=self.base + "/",
                       temperature=0.2, max_tokens=None, response_format={"type": "json_object"})
        self.assertEqual(out, "hi m1")
        method, path, headers, body = _Handler.calls[-1]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers.get("Authorization"), "Bearer k")
        self.assertEqual(body["temperature"], 0.2)
        self.assertNotIn("max_tokens", body, "None 参数不应发出去")
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_chat_http_error_has_code_and_retries(self):
        with self.assertRaisesRegex(llm.LLMError, r"HTTP 401: .*invalid key"):
            llm.chat([], "bad", base_url=self.base, retries=2)
        self.assertEqual(len(_Handler.calls), 3)

    def test_chat_bad_shape(self):
        with self.assertRaisesRegex(llm.LLMError, "响应格式不对"):
            llm.chat([], "shape", base_url=self.base)

    def test_chat_missing_key(self):
        with self.assertRaises(llm.LLMError):
            llm.chat([], "")

    def test_extract_json(self):
        self.assertEqual(llm.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.extract_json('前言 [1, 2] 结尾', kind="array"), [1, 2])
        with self.assertRaises(ValueError):
            llm.extract_json("no json here")

    def test_find_api_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cred = os.path.join(tmp, "cred.yaml")
            with open(cred, "w") as f:
                f.write("refs:\n  DEEPSEEK_API_KEY: from-dsh\n")
            with mock.patch.object(llm, "DSH_CREDENTIALS", cred), \
                    mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
                self.assertEqual(llm.find_api_key(), "from-dsh")
                self.assertEqual(llm.find_api_key(dsh_fallback=False), "")
                self.assertEqual(llm.find_api_key(env={"DEEPSEEK_API_KEY": "from-env"}), "from-env")
                with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "from-os"}):
                    self.assertEqual(llm.find_api_key(), "from-os")
                    self.assertEqual(llm.find_api_key(env={}, use_os_environ=False), "from-dsh")
                self.assertEqual(llm.find_api_key(names=("LLM_API_KEY", "DEEPSEEK_API_KEY"),
                                                  env={"LLM_API_KEY": "llm"}), "llm")


if __name__ == "__main__":
    unittest.main()
