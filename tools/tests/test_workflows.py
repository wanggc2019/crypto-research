# -*- coding: utf-8 -*-
"""CI workflow 约束：拿得到订阅 token 的 AI 审查 job 只装按哈希锁定的最小依赖，Claude Code 锁定单一版本。"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ai-review.yml"
AI_REQ = REPO / ".github" / "ai-review-requirements.txt"
ROOT_REQ = REPO / "requirements.txt"
ADR = REPO / "docs" / "adr" / "0011-llm-claude.md"


def norm(name):
    """PEP 503 包名归一化。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_entries(text):
    """[(包名, 版本或 None, 哈希数)]；处理 \\ 续行与注释。"""
    joined = re.sub(r"\\\s*\n", " ", text)
    out = []
    for line in joined.splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith("--hash") else line.strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(?:==\s*([0-9][^\s;\\]*))?", line)
        out.append((norm(m.group(1)), m.group(2), len(re.findall(r"--hash=sha256:[0-9a-f]{64}", line))))
    return out


def run_commands(workflow_text):
    """workflow 里所有 run: 的命令文本（处理 | 多行块与 \\ 续行）。"""
    cmds, block, indent = [], None, None
    for line in workflow_text.splitlines():
        if block is not None:
            cur = len(line) - len(line.lstrip())
            if line.strip() and cur <= indent:
                cmds.append("\n".join(block))
                block = None
            else:
                block.append(line.strip())
                continue
        m = re.match(r"(\s*)(?:- )?run:\s*(.*)$", line)
        if m:
            if m.group(2).strip() in ("|", ">"):
                block, indent = [], len(m.group(1))
            else:
                cmds.append(m.group(2))
    if block:
        cmds.append("\n".join(block))
    return [re.sub(r"\\\s*\n", " ", c) for c in cmds]


class AiReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.wf = WORKFLOW.read_text(encoding="utf-8")
        self.cmds = run_commands(self.wf)

    def test_pip_installs_only_hash_locked_file(self):
        pip = [line for c in self.cmds for line in c.splitlines() if re.search(r"\bpip3?\s+install\b|-m\s+pip\s+install", line)]
        self.assertEqual(len(pip), 1, "审查 job 只允许一处 pip 安装：%s" % pip)
        line = pip[0]
        for flag in ("--no-deps", "--require-hashes", "-r .github/ai-review-requirements.txt"):
            self.assertIn(flag, line)
        rest = re.sub(r"-r\s+\S+|--\S+", "", line.split("install", 1)[1]).split()
        self.assertEqual(rest, [], "除了锁定文件之外不得直接装包：%s" % rest)

    def test_root_requirements_never_installed(self):
        self.assertIsNone(re.search(r"(-r|--requirement)[=\s]+\.?/?requirements\.txt", self.wf))

    def test_ai_requirements_pinned_hashed_and_consistent(self):
        root = {n: v for n, v, _ in requirement_entries(ROOT_REQ.read_text(encoding="utf-8"))}
        entries = requirement_entries(AI_REQ.read_text(encoding="utf-8"))
        self.assertTrue(entries)
        for name, version, hashes in entries:
            self.assertTrue(version, "%s 必须 == 锁版本" % name)
            self.assertGreaterEqual(hashes, 1, "%s 必须带 sha256 哈希" % name)
            self.assertEqual(root.get(name), version, "%s 版本应与 requirements.txt 一致" % name)

    def test_claude_code_single_pinned_version(self):
        refs = re.findall(r"@anthropic-ai/claude-code(@[^\s\"']*)?", self.wf)
        self.assertTrue(refs, "workflow 应安装 Claude Code")
        versions = set(refs)
        self.assertTrue(all(re.fullmatch(r"@\d+\.\d+\.\d+", v or "") for v in versions), "每处都必须写死版本：%s" % versions)
        self.assertEqual(len(versions), 1, "只能有一个版本：%s" % versions)
        self.assertIn(versions.pop().lstrip("@"), ADR.read_text(encoding="utf-8"), "ADR 0011 里写的版本应与 workflow 一致")

    def test_parser_catches_loopholes(self):
        bad = requirement_entries("PyYAML==6.0.3 --hash=sha256:%s\nrequests\n" % ("a" * 64))
        self.assertIn(("requests", None, 0), bad)
        self.assertEqual(norm("Typing_Extensions"), "typing-extensions")
        cmds = run_commands("    steps:\n      - run: |\n          pip3 install \\\n            requests\n")
        self.assertIn("requests", cmds[0])


if __name__ == "__main__":
    unittest.main()
