# -*- coding: utf-8 -*-
"""tools/complexity_check.py：复杂度棘轮的比较逻辑 + 真实 ruff 测量。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import complexity_check as cc  # noqa: E402


class CompareTest(unittest.TestCase):
    def test_new_function_over_limit_fails(self):
        errors, _ = cc.compare({"a.py::f": 16}, {})
        self.assertEqual(len(errors), 1)
        self.assertIn("新超标", errors[0])

    def test_baseline_function_growing_fails(self):
        errors, _ = cc.compare({"a.py::f": 21}, {"a.py::f": 20})
        self.assertEqual(len(errors), 1)
        self.assertIn("只许变简单", errors[0])

    def test_equal_passes_and_improvement_is_reported(self):
        errors, improved = cc.compare({"a.py::f": 20, "b.py::g": 17}, {"a.py::f": 20, "b.py::g": 19, "c.py::h": 30})
        self.assertEqual(errors, [])
        self.assertEqual(len(improved), 2)

    def test_missing_path_is_an_error_not_silent_pass(self):
        self.assertEqual(cc.main(["no-such-dir-xyz"]), 2)


class MeasureTest(unittest.TestCase):
    def setUp(self):
        if not cc._ruff():
            self.skipTest("无 ruff")
        self.tmp = tempfile.mkdtemp(prefix="cc-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_measures_only_functions_over_limit(self):
        branches = "".join("    if x == %d:\n        return %d\n" % (i, i) for i in range(20))
        with open(os.path.join(self.tmp, "m.py"), "w") as f:
            f.write("def big(x):\n%s    return -1\n\n\ndef small(x):\n    return x\n" % branches)
        found = cc.measure([self.tmp])
        rel = os.path.relpath(os.path.join(self.tmp, "m.py"), cc.REPO)
        self.assertEqual(list(found), [rel + "::big"])
        self.assertGreater(found[rel + "::big"], cc.MAX)


if __name__ == "__main__":
    unittest.main()
