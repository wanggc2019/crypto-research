# -*- coding: utf-8 -*-
"""模块业务代码不得直接 urllib.request.urlopen：HTTP 走 crypto_core.http，LLM 走 crypto_core.llm。"""
import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = re.compile(r"\burlopen\(")


class NoRawUrlopenTest(unittest.TestCase):
    def test_modules_use_shared_http(self):
        offenders = []
        for path in sorted(glob.glob(os.path.join(REPO, "crypto-*", "scripts", "*.py"))):
            with open(path, encoding="utf-8") as f:
                for no, line in enumerate(f, 1):
                    if RAW.search(line) and not line.lstrip().startswith("#"):
                        offenders.append("%s:%d" % (os.path.relpath(path, REPO), no))
        self.assertEqual(offenders, [], "改用 crypto_core.http / crypto_core.llm")


if __name__ == "__main__":
    unittest.main()
