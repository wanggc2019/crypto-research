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
        if self.path == "/ds/chat/completions":  # OpenAI 兼容（DeepSeek）
            if self.headers.get("Authorization") == "Bearer bad":
                return self._send(401, {"error": "invalid key"})
            return self._send(200, {"choices": [{"message": {"content": "ds " + body["model"]}}]})
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

    # ---------------- config/ai.yaml 路由
    CFG = {
        "providers": {"anthropic": {"label": "Claude", "key_env": "ANTHROPIC_API_KEY"},
                      "deepseek": {"label": "DeepSeek", "key_env": "DEEPSEEK_API_KEY",
                                   "base_url": "http://unused", "default_model": "deepseek-chat"}},
        "defaults": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "medium"},
        "tasks": {"a.fast": {"model": "claude-haiku-5-5", "effort": "low"},
                  "a.inherit": {},
                  "d.cheap": {"provider": "deepseek"},
                  "d.named": {"provider": "deepseek", "model": "deepseek-reasoner", "effort": "high"}},
    }

    def test_resolve_inherits_defaults_within_provider(self):
        spec = llm.resolve("a.inherit", cfg=self.CFG)
        self.assertEqual((spec["provider"], spec["model"], spec["effort"], spec["key_env"]),
                         ("anthropic", "claude-opus-5-5", "medium", "ANTHROPIC_API_KEY"))
        spec = llm.resolve("a.fast", cfg=self.CFG)
        self.assertEqual((spec["model"], spec["effort"]), ("claude-haiku-5-5", "low"))

    def test_resolve_other_provider_does_not_inherit_model(self):
        spec = llm.resolve("d.cheap", cfg=self.CFG)
        self.assertEqual((spec["provider"], spec["model"], spec["effort"], spec["label"]),
                         ("deepseek", "deepseek-chat", None, "DeepSeek"))
        self.assertIsNone(llm.resolve("d.named", cfg=self.CFG)["effort"], "deepseek 不支持 effort")

    def test_resolve_unknown_task_and_validate(self):
        with self.assertRaisesRegex(llm.LLMError, "没有登记"):
            llm.resolve("nope", cfg=self.CFG)
        bad = dict(self.CFG, tasks={"x": {"provider": "openai"}, "y": {"effort": "huge"}})
        problems = llm.validate_config(bad, tasks_in_code=["x", "z"])
        self.assertEqual(len(problems), 3, problems)
        self.assertEqual(llm.validate_config(self.CFG, tasks_in_code=["a.fast"]), [])

    def test_complete_routes_to_deepseek(self):
        spec = llm.resolve("d.cheap", cfg=self.CFG)
        out = llm.complete(spec, [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}], "k",
                           base_url=self.base + "/ds", retries=0)
        self.assertEqual(out, "ds deepseek-chat")
        _, path, headers, body = _Handler.calls[-1]
        self.assertEqual(path, "/ds/chat/completions")
        self.assertEqual(headers.get("Authorization"), "Bearer k")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "s"}, "OpenAI 兼容保留 system 消息")
        self.assertLessEqual(body["max_tokens"], 8192)
        before = len(_Handler.calls)
        with self.assertRaisesRegex(llm.LLMError, "HTTP 401"):
            llm.complete(spec, [], "bad", base_url=self.base + "/ds", retries=3)
        self.assertEqual(len(_Handler.calls) - before, 1, "4xx 不重试")

    def test_complete_routes_to_anthropic(self):
        spec = llm.resolve("a.fast", cfg=self.CFG)
        out = llm.complete(spec, [{"role": "user", "content": "x"}], "k", base_url=self.base, retries=0)
        self.assertEqual(out, "hi claude-haiku-5-5")
        self.assertEqual(self._last_llm_call()[3]["output_config"], {"effort": "low"})

    def test_chat_for_without_key_raises(self):
        with mock.patch.object(llm, "load_ai_config", return_value=self.CFG), \
                mock.patch.object(llm._env, "load_env", lambda: {}), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaisesRegex(llm.LLMError, "missing ANTHROPIC_API_KEY"):
                llm.chat_for("a.fast", [{"role": "user", "content": "x"}])

    # ---------------- claude-code（订阅，claude -p）：用假的 claude 脚本，不真调
    def _fake_claude(self, tmp, payload):
        script = os.path.join(tmp, "claude")
        with open(script, "w") as f:
            f.write("#!/bin/sh\n"
                    "printf '%%s\\n' \"$@\" > \"%s/args\"\n"
                    "cat > \"%s/stdin\"\n"
                    "env > \"%s/env\"\n"
                    "cat <<'EOF'\n%s\nEOF\n" % (tmp, tmp, tmp, payload))
        os.chmod(script, 0o755)
        return script

    def test_claude_code_success_args_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self._fake_claude(tmp, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                                      "stop_reason": "end_turn", "result": " 你好 "}))
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "should-be-removed"}):
                out = llm._claude_code_chat([{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}],
                                            "claude-haiku-5-5", effort="low", binary=fake)
            self.assertEqual(out, "你好")
            args = open(os.path.join(tmp, "args")).read().split("\n")
            self.assertEqual(args[args.index("--tools") + 1], "", "不给任何工具")
            self.assertEqual(args[args.index("--model") + 1], "claude-haiku-5-5")
            self.assertEqual(args[args.index("--effort") + 1], "low")
            self.assertEqual(args[args.index("--system-prompt") + 1], "SYS")
            for flag in ("-p", "--no-session-persistence", "--output-format"):
                self.assertIn(flag, args)
            self.assertEqual(open(os.path.join(tmp, "stdin")).read(), "Q")
            self.assertNotIn("ANTHROPIC_API_KEY", open(os.path.join(tmp, "env")).read(), "订阅模式不能带 API key")

    def test_claude_code_errors(self):
        cases = [
            ({"subtype": "error_during_execution", "is_error": True, "result": "quota"}, "失败"),
            ({"subtype": "success", "is_error": False, "stop_reason": "refusal", "result": "x"}, "拒绝"),
            ({"subtype": "success", "is_error": False, "stop_reason": "end_turn", "result": "  "}, "没有返回文本"),
        ]
        for payload, pattern in cases:
            with tempfile.TemporaryDirectory() as tmp:
                fake = self._fake_claude(tmp, json.dumps(payload))
                with self.assertRaisesRegex(llm.LLMError, pattern):
                    llm._claude_code_chat([{"role": "user", "content": "x"}], "m", binary=fake)
        with tempfile.TemporaryDirectory() as tmp:
            fake = self._fake_claude(tmp, "not json")
            with self.assertRaisesRegex(llm.LLMError, "退出码"):
                llm._claude_code_chat([{"role": "user", "content": "x"}], "m", binary=fake)

    def test_claude_code_key_for_depends_on_binary(self):
        spec = {"provider": "claude-code", "key_env": ""}
        with mock.patch.object(llm, "claude_binary", return_value="/x/claude"):
            self.assertEqual(llm.key_for(spec), llm.SUBSCRIPTION)
        with mock.patch.object(llm, "claude_binary", return_value=""):
            self.assertEqual(llm.key_for(spec), "")

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
