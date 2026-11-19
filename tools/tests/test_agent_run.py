# -*- coding: utf-8 -*-
"""agent_run.py：命令构造、计费方式（订阅/按量）、拒绝清单、配置校验（不启动任何 agent）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "crypto_core" / "src"))

import agent_run  # noqa: E402
from crypto_core import llm  # noqa: E402

CFG = {"agents": {
    "defaults": {"runner": "claude", "auth": "subscription", "model": "claude-opus-5-5"},
    "tasks": {
        "t.sub": {"allowed_tools": ["Read", "Bash(rm x.lock)"]},
        "t.api": {"auth": "api", "model": "claude-haiku-5-5", "effort": "low", "allowed_tools": ["Read"]},
        "t.dsh": {"runner": "dsh"},
        "t.bad": {"runner": "codex"},
        "t.notools": {},
    },
}}


class AgentRunTest(unittest.TestCase):
    def test_claude_command_subscription(self):
        spec = llm.resolve_agent("t.sub", cfg=CFG)
        cmd = agent_run.claude_command(spec, "claude")
        for flag in ("-p", "--no-session-persistence", "--permission-prompts", "none", "--setting-sources",
                     "project,local"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--bare", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(cmd[-3:], ["--allowedTools", "Read", "Bash(rm x.lock)"], "allowedTools 必须在最后")
        deny = cmd[cmd.index("--disallowedTools") + 1: cmd.index("--allowedTools")]
        self.assertEqual(deny, agent_run.ALWAYS_DENY)
        self.assertIn("Read(**/.env)", deny)
        self.assertIn("WebFetch", deny)

    def test_claude_command_api_uses_bare_and_effort(self):
        cmd = agent_run.claude_command(llm.resolve_agent("t.api", cfg=CFG), "claude")
        self.assertIn("--bare", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "low")

    def test_subscription_env_strips_api_key(self):
        spec = llm.resolve_agent("t.sub", cfg=CFG)
        env, err = agent_run.claude_env(spec, base_env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t",
                                                        "PATH": "/usr/bin"})
        self.assertEqual(err, "")
        self.assertNotIn("ANTHROPIC_API_KEY", env, "订阅模式下留着 key 会被改成按量计费")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertIn(".local/bin", env["PATH"])

    def test_api_env_needs_key(self):
        spec = llm.resolve_agent("t.api", cfg=CFG)
        env, err = agent_run.claude_env(spec, base_env={}, find_key=lambda: "")
        self.assertIn("没有 ANTHROPIC_API_KEY", err)
        env, err = agent_run.claude_env(spec, base_env={}, find_key=lambda: "sk-ant-x")
        self.assertEqual((err, env["ANTHROPIC_API_KEY"]), ("", "sk-ant-x"))

    def test_dsh_command_reuses_session_helper(self):
        cmd = agent_run.dsh_command(llm.resolve_agent("t.dsh", cfg=CFG), "/tmp/p.md")
        self.assertEqual(cmd[:2], ["/bin/bash", "-c"])
        self.assertTrue(cmd[4].endswith("tools/headless_session.sh"))
        self.assertEqual(cmd[6:], ["data/state/headless-session-t.dsh", "/tmp/p.md"])

    def test_resolve_agent_validation(self):
        with self.assertRaisesRegex(llm.LLMError, "runner"):
            llm.resolve_agent("t.bad", cfg=CFG)
        with self.assertRaisesRegex(llm.LLMError, "allowed_tools"):
            llm.resolve_agent("t.notools", cfg=CFG)
        with self.assertRaisesRegex(llm.LLMError, "没有登记"):
            llm.resolve_agent("nope", cfg=CFG)
        self.assertIsNotNone(llm.resolve_agent("t.dsh", cfg=CFG), "dsh 不需要 allowed_tools")
        bad = {"agents": {"defaults": {}, "tasks": {"w": {"allowed_tools": ["Write(a.txt)"]}}}}
        with self.assertRaisesRegex(llm.LLMError, "Edit"):
            llm.resolve_agent("w", cfg=bad)

    def test_offline_refuses_real_launch_but_allows_dry_run(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {llm.OFFLINE_ENV: "1"}), \
             mock.patch.object(agent_run.subprocess, "run") as run:
            self.assertEqual(agent_run.main(["news.inspect", __file__]), 2)
            run.assert_not_called()

    def test_main_rejects_unknown_task_and_missing_prompt(self):
        self.assertEqual(agent_run.main(["nope.task", __file__, "--dry-run"]), 2)
        self.assertEqual(agent_run.main(["news.inspect", "/no/such/prompt.md", "--dry-run"]), 2)


if __name__ == "__main__":
    unittest.main()
