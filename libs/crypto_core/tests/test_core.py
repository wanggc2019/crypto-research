#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto_core 基础件单测（路径 / 凭据 / 配置 / 时间 / 存储 / 文本）。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from crypto_core import config, env, paths, store, text, timeutil


class TestPaths(unittest.TestCase):
    def test_repo_root_contains_libs(self):
        root = paths.repo_root()
        self.assertTrue((root / "libs" / "crypto_core" / "src" / "crypto_core").is_dir())
        self.assertTrue((root / "README.md").is_file())

    def test_module_helpers(self):
        with tempfile.TemporaryDirectory() as d:
            paths.ensure_dirs(Path(d) / "a", Path(d) / "b")
            self.assertTrue((Path(d) / "a").is_dir())
            self.assertTrue((Path(d) / "b").is_dir())

    def test_find_repo_root_from_module_file(self):
        root = paths.repo_root()
        self.assertEqual(paths.find_repo_root(root / "crypto-news" / "scripts"), root)


class TestEnv(unittest.TestCase):
    def test_load_and_save(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".env", delete=False) as f:
            f.write("# comment\nA=1\nB='x y'\n")
            path = f.name
        try:
            self.assertEqual(env.load_env(path), {"A": "1", "B": "x y"})
            env.save_env_value("C", "3", path)
            env.save_env_value("A", "9", path)
            got = env.load_env(path)
            self.assertEqual(got["A"], "9")
            self.assertEqual(got["C"], "3")
            self.assertIn("# comment", Path(path).read_text(encoding="utf-8"))
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        self.assertEqual(env.load_env("/nonexistent/x.env"), {})

    def test_find_env_falls_back_to_repo(self):
        repo_env = paths.repo_root() / ".env"
        found = env.find_env_file(module_dir=None)
        self.assertEqual(found, repo_env)


class TestConfig(unittest.TestCase):
    def test_load_yaml_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "a.yaml"
            y.write_text("schema_version: 1\nname: x\n", encoding="utf-8")
            self.assertEqual(config.load_yaml(y)["name"], "x")
            j = Path(d) / "b.json"
            j.write_text(json.dumps({"k": 1}), encoding="utf-8")
            self.assertEqual(config.load_json(j), {"k": 1})
            self.assertEqual(config.load_yaml(Path(d) / "missing.yaml"), {})

    def test_load_config_by_module_dir(self):
        module_dir = paths.repo_root() / "crypto-news"
        cfg = config.load_config(module_dir, "watchlist.yaml")
        self.assertIn("assets", cfg)

    def test_schema_version(self):
        self.assertIsNone(config.check_schema_version({"schema_version": 1}))
        self.assertIsNotNone(config.check_schema_version({}))
        self.assertIsNotNone(config.check_schema_version({"schema_version": 2}))


class TestTime(unittest.TestCase):
    def test_quiet_hours(self):
        import datetime

        self.assertTrue(timeutil.in_quiet_hours(datetime.datetime(2026, 11, 8, 23, 30)))
        self.assertTrue(timeutil.in_quiet_hours(datetime.datetime(2026, 11, 8, 3, 0)))
        self.assertFalse(timeutil.in_quiet_hours(datetime.datetime(2026, 11, 8, 12, 0)))
        self.assertFalse(timeutil.in_quiet_hours(datetime.datetime(2026, 11, 8, 23, 30), span="off"))
        self.assertFalse(timeutil.in_quiet_hours(datetime.datetime(2026, 11, 8, 23, 30), span=""))

    def test_iso_and_today(self):
        import datetime

        self.assertRegex(timeutil.ts_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertRegex(timeutil.today_str(), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(timeutil.today_str(datetime.datetime(2026, 1, 2)), "2026-01-02")

    def test_decay(self):
        self.assertAlmostEqual(timeutil.decay(0, 24), 1.0, places=6)
        self.assertAlmostEqual(timeutil.decay(24, 24), 0.5, places=6)


class TestStore(unittest.TestCase):
    def test_json_roundtrip_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "state.json"
            store.safe_json_dump(p, {"a": 1})
            self.assertEqual(store.safe_json_load(p, {}), {"a": 1})
            self.assertEqual(store.safe_json_load(Path(d) / "nope.json", {"d": 1}), {"d": 1})
            self.assertFalse((Path(d) / "sub" / "state.json.tmp").exists())

    def test_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "items.jsonl"
            store.append_jsonl(p, [{"i": 1}, {"i": 2}])
            store.append_jsonl(p, {"i": 3})
            rows = store.read_jsonl(p)
            self.assertEqual([r["i"] for r in rows], [1, 2, 3])


class TestLocalTime(unittest.TestCase):
    def test_fmt_local_no_zone(self):
        # 仓库不输出地理位置/时区名，避免暴露所在地
        self.assertNotIn("本地时区", timeutil.fmt_local("2026-11-09T20:00:00+00:00", "%H:%M"))
        self.assertNotIn("UTC", timeutil.now_label())
        self.assertRegex(timeutil.now_label(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class TestText(unittest.TestCase):
    def test_clean_and_url(self):
        self.assertEqual(text.clean_text("<b>a</b>&amp;  b"), "a & b")
        self.assertEqual(text.norm_url("https://x.com/a?utm_source=t&ref=1"), "https://x.com/a")
        self.assertTrue(text.contains_any("Binance paused withdrawals", ["paused"]))
        self.assertEqual(text.sha256("x"), text.sha256("x"))

    def test_norm_title(self):
        self.assertEqual(text.norm_title("Hello,  World!!"), "hello world")


if __name__ == "__main__":
    unittest.main(verbosity=2)
