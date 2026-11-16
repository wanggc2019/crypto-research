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
        if self.path.split("?")[0] == "/v1/messages":
            auth = self.headers.get("Authorization") or self.headers.get("X-Api-Key") or ""
            if auth.endswith("bad"):
                return self._send(401, {"type": "error", "error": {"type": "authentication_error",
                                                                   "message": "invalid key"}})
            stop = {"refuse": "refusal", "cut": "max_tokens"}.get(auth.rsplit(" ", 1)[-1], "end_turn")
            return self._send(200, {
                "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": "hi " + body["model"]}],
                "stop_reason": stop, "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
            })
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

    # ---------------- llm（Anthropic SDK 指向本地假服务）
    def _last_llm_call(self):
        return [c for c in _Handler.calls if c[1].startswith("/v1/messages")][-1]

    def test_chat_haiku_plain_request(self):
        out = llm.chat([{"role": "system", "content": "S1"}, {"role": "user", "content": "x"}], "k",
                       model=llm.FAST_MODEL, effort="low", base_url=self.base, retries=0)
        self.assertEqual(out, "hi " + llm.FAST_MODEL)
        _, path, headers, body = self._last_llm_call()
        self.assertEqual(path, "/v1/messages", "Haiku 不走 beta 回退")
        self.assertEqual(body["system"], "S1")
        self.assertEqual(body["messages"], [{"role": "user", "content": "x"}])
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["max_tokens"], llm.DEFAULT_MAX_TOKENS)
        for banned in ("temperature", "top_p", "top_k", "fallbacks"):
            self.assertNotIn(banned, body)
        self.assertNotIn("k", headers.get("User-Agent", ""))

    def test_chat_opus_uses_server_side_fallback(self):
        out = llm.chat([{"role": "user", "content": "x"}], "k", base_url=self.base, retries=0)
        self.assertEqual(out, "hi " + llm.DEFAULT_MODEL)
        _, path, headers, body = self._last_llm_call()
        self.assertEqual(path.split("?")[0], "/v1/messages")
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn(llm.FALLBACK_BETA, headers.get("anthropic-beta", ""))
        self.assertNotIn("output_config", body)

    def test_chat_http_error_has_code_and_retries(self):
        before = len(_Handler.calls)
        with self.assertRaisesRegex(llm.LLMError, r"HTTP 401: .*invalid key"):
            llm.chat([{"role": "user", "content": "x"}], "bad", model=llm.FAST_MODEL,
                     base_url=self.base, retries=2)
        self.assertEqual(len(_Handler.calls) - before, 1, "401 不重试")

    def test_chat_refusal_and_truncation_raise(self):
        with self.assertRaisesRegex(llm.LLMError, "拒绝"):
            llm.chat([{"role": "user", "content": "x"}], "refuse", model=llm.FAST_MODEL,
                     base_url=self.base, retries=0)
        with self.assertRaisesRegex(llm.LLMError, "截断"):
            llm.chat([{"role": "user", "content": "x"}], "cut", model=llm.FAST_MODEL,
                     base_url=self.base, retries=0)

    def test_chat_missing_key(self):
        with self.assertRaises(llm.LLMError):
            llm.chat([], "")

    def test_split_system(self):
        system, rest = llm._split_system([{"role": "system", "content": "a"}, {"role": "system", "content": "b"},
                                          {"role": "user", "content": "u"}])
        self.assertEqual(system, "a\n\nb")
        self.assertEqual(rest, [{"role": "user", "content": "u"}])

    def test_extract_json(self):
        self.assertEqual(llm.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.extract_json('前言 [1, 2] 结尾', kind="array"), [1, 2])
        with self.assertRaises(ValueError):
            llm.extract_json("no json here")

    def test_find_api_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            envfile = os.path.join(tmp, ".env")
            with open(envfile, "w") as f:
                f.write("ANTHROPIC_API_KEY=from-file\n")
            with mock.patch.object(llm._env, "load_env", lambda: {"ANTHROPIC_API_KEY": "from-file"}), \
                    mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
                self.assertEqual(llm.find_api_key(), "from-file")
                self.assertEqual(llm.find_api_key(use_env_file=False), "")
                self.assertEqual(llm.find_api_key(env={"ANTHROPIC_API_KEY": "from-env"}), "from-env")
                with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-os"}):
                    self.assertEqual(llm.find_api_key(), "from-os")
                    self.assertEqual(llm.find_api_key(env={}, use_os_environ=False), "from-file")
                self.assertEqual(llm.find_api_key(names=("OTHER", "ANTHROPIC_API_KEY"),
                                                  env={"OTHER": "o"}), "o")

if __name__ == "__main__":
    unittest.main()
