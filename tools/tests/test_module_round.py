# -*- coding: utf-8 -*-
"""tools/lib/module_round.sh：launchd 一轮骨架的行为测试（临时假仓库 + 桩 healthcheck/prune）。"""
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO, "tools", "lib", "module_round.sh")
START_RE = re.compile(r"^===== \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} start =====$", re.M)
DONE_RE = re.compile(r"^===== \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} done \(rc=(\d+)\) =====$", re.M)

STUB_TOOL = """import os, sys
with open(os.path.join(os.environ["ROUND_TEST_OUT"], "%s.calls"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + " label=" + os.environ.get("CRYPTO_JOB_LABEL", "") + "\\n")
"""


class ModuleRoundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="round-test-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "tools", "lib"))
        shutil.copy(LIB, os.path.join(self.repo, "tools", "lib", "module_round.sh"))
        for name in ("healthcheck", "prune"):
            with open(os.path.join(self.repo, "tools", name + ".py"), "w") as f:
                f.write(STUB_TOOL % name)
        self.mod = os.path.join(self.repo, "crypto-fake")
        os.makedirs(os.path.join(self.mod, "scripts"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect(self, body, opts="", extra=""):
        path = os.path.join(self.mod, "scripts", "collect.sh")
        with open(path, "w") as f:
            f.write('#!/usr/bin/env bash\nsource "$(dirname "$0")/../../tools/lib/module_round.sh"\n'
                    "body() {\n%s\n}\n%s\nround_main body %s\n" % (body, extra, opts))
        os.chmod(path, 0o755)
        return path

    def _run(self, path, env_extra=None, cwd=None):
        env = {k: v for k, v in os.environ.items() if k != "CRYPTO_JOB_LABEL"}
        env["ROUND_TEST_OUT"] = self.tmp
        env.update(env_extra or {})
        # 从别的 cwd 调用，验证骨架自己 cd 到模块目录（launchd 的 cwd 不可控）
        return subprocess.run(["bash", path], cwd=cwd or self.tmp, env=env, capture_output=True, text=True,
                              timeout=30)

    def _log(self):
        with open(os.path.join(self.mod, "data", "logs", "collect.log")) as f:
            return f.read()

    def _calls(self, name):
        p = os.path.join(self.tmp, name + ".calls")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()

    def test_rc_propagates_and_log_format(self):
        proc = self._run(self._collect('echo "hello from $MODULE in $(basename "$PWD")"; return 3'))
        self.assertEqual(proc.returncode, 3)
        log = self._log()
        self.assertEqual(len(START_RE.findall(log)), 1)
        self.assertEqual(DONE_RE.findall(log), ["3"])
        self.assertIn("hello from crypto-fake in crypto-fake", log)
        self.assertFalse(os.path.exists(os.path.join(self.mod, "data", "state", "collect.lock")), "锁未释放")

    def test_healthcheck_after_round_with_extra_args_and_default_label(self):
        self._run(self._collect("true", '--health-args "--agent"'))
        calls = self._calls("healthcheck")
        self.assertEqual(len(calls), 1)
        self.assertIn("--module crypto-fake --quiet --notify --fix --agent", calls[0])
        self.assertIn("label=com.crypto-research.crypto-fake", calls[0])

    def test_injected_label_is_kept(self):
        self._run(self._collect("true"), {"CRYPTO_JOB_LABEL": "com.crypto-research.crypto-fake.alert"})
        self.assertIn("label=com.crypto-research.crypto-fake.alert", self._calls("healthcheck")[0])

    def test_prune_once_per_day(self):
        path = self._collect("true")
        self._run(path)
        self._run(path)
        self.assertEqual(len(self._calls("prune")), 1)
        self.assertEqual(len(self._calls("healthcheck")), 2)

    def test_lock_held_skips_round(self):
        os.makedirs(os.path.join(self.mod, "data", "state", "collect.lock"))
        os.makedirs(os.path.join(self.mod, "data", "logs"))
        proc = self._run(self._collect("echo SHOULD_NOT_RUN"))
        self.assertEqual(proc.returncode, 0)
        log = self._log()
        self.assertIn("skip: previous run still in progress", log)
        self.assertNotIn("SHOULD_NOT_RUN", log)
        self.assertEqual(self._calls("healthcheck"), [])
        self.assertTrue(os.path.isdir(os.path.join(self.mod, "data", "state", "collect.lock")), "不能删别人的锁")

    def test_no_lock_ignores_existing_lock(self):
        os.makedirs(os.path.join(self.mod, "data", "state", "collect.lock"))
        proc = self._run(self._collect("echo RAN", "--no-lock"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("RAN", self._log())

    def _heartbeat(self, age_min):
        state = os.path.join(self.repo, "data", "state")
        os.makedirs(state, exist_ok=True)
        path = os.path.join(state, "watchdog.json")
        with open(path, "w") as f:
            f.write("{}")
        t = time.time() - age_min * 60
        os.utime(path, (t, t))

    def test_fresh_watchdog_owns_healthcheck(self):
        self._heartbeat(age_min=1)
        proc = self._run(self._collect("return 0"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._calls("healthcheck"), [], "watchdog 活着时本轮不应重复健康检查")
        self.assertEqual(len(self._calls("prune")), 1, "prune 仍由本轮负责")

    def test_stale_watchdog_falls_back_to_self_check(self):
        self._heartbeat(age_min=30)
        self._run(self._collect("return 0"))
        self.assertEqual(len(self._calls("healthcheck")), 1, "watchdog 心跳过期时本轮兜底自查")

    def test_healthcheck_failure_does_not_change_rc(self):
        with open(os.path.join(self.repo, "tools", "healthcheck.py"), "w") as f:
            f.write("import sys\nsys.exit(2)\n")
        proc = self._run(self._collect("return 0"))
        self.assertEqual(proc.returncode, 0)


    def test_relative_path_invocation(self):
        self._collect('echo "in $(basename "$PWD")"')
        proc = self._run(os.path.join("scripts", "collect.sh"), cwd=self.mod)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("in crypto-fake", self._log())

    def test_after_hook_runs_after_health_and_prune(self):
        extra = 'after() { echo "AFTER_HOOK $(wc -l < "$ROUND_TEST_OUT/healthcheck.calls")"; return 7; }'
        proc = self._run(self._collect("return 0", extra=extra))
        self.assertEqual(proc.returncode, 0, "after 失败不影响 rc")
        log = self._log()
        self.assertRegex(log, r"AFTER_HOOK\s+1")
        self.assertLess(log.index("done (rc=0)"), log.index("AFTER_HOOK"))

    def test_refuses_non_module_directory(self):
        bad = os.path.join(self.repo, "notamodule", "scripts")
        os.makedirs(bad)
        path = os.path.join(bad, "collect.sh")
        with open(path, "w") as f:
            f.write('source "$(dirname "$0")/../../tools/lib/module_round.sh"\nbody() { true; }\nround_main body\n')
        proc = self._run(path)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("crypto-*", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "notamodule", "data")), "不应在错误目录写任何东西")


class AllCollectScriptsUseRoundTest(unittest.TestCase):
    def test_every_module_collect_uses_shared_round(self):
        for name in sorted(os.listdir(REPO)):
            path = os.path.join(REPO, name, "scripts", "collect.sh")
            if not (name.startswith("crypto-") and os.path.isfile(path)):
                continue
            with self.subTest(module=name):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertIn('source "$(dirname "$0")/../../tools/lib/module_round.sh"', text)
                self.assertRegex(text, r"(?m)^round_main body\b")
                self.assertNotIn("mkdir \"$LOCKDIR\"", text, "锁应由 module_round.sh 统一处理")
                self.assertTrue(os.access(path, os.X_OK), "collect.sh 必须可执行")


if __name__ == "__main__":
    unittest.main()
