# -*- coding: utf-8 -*-
"""CI workflow 约束：
- 拿得到订阅 token 的 AI 审查 job 只装按哈希锁定的最小依赖，Claude Code 锁定单一版本；
- 私有仓库 Actions 分钟（docs/adr/0012）：检查合成一个 job、合并到 main 不重跑、AI 审查不随每次推送跑。
"""
import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ai-review.yml"
AI_REQ = REPO / ".github" / "ai-review-requirements.txt"
ROOT_REQ = REPO / "requirements.txt"
ADR = REPO / "docs" / "adr" / "0011-llm-claude.md"
WORKFLOWS = REPO / ".github" / "workflows"


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


def load(path):
    """(on 段 dict, 整个文档)；PyYAML 按 YAML 1.1 会把 on 解析成 True；on 可写成 str/list/dict。"""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {k: None for k in on}
    return dict(on or {}), doc


def steps(doc, job):
    return doc["jobs"][job]["steps"]


class ActionsMinutesTest(unittest.TestCase):
    def setUp(self):
        self.ci_on, self.ci = load(WORKFLOWS / "ci.yml")
        self.ai_on, self.ai = load(WORKFLOWS / "ai-review.yml")

    def test_only_two_workflows(self):
        names = sorted(p.name for p in WORKFLOWS.glob("*.y*ml"))
        self.assertEqual(names, ["ai-review.yml", "ci.yml"], "新增 workflow 会多一个按分钟取整计费的 job，先看 ADR 0012")

    def test_no_workflow_runs_on_push(self):
        for p in WORKFLOWS.glob("*.y*ml"):
            self.assertNotIn("push", load(p)[0], "%s：合并到 main 不重跑（PR 上与 pre-push 已跑过）" % p.name)

    def test_ci_is_single_job_with_all_checks(self):
        self.assertEqual(list(self.ci["jobs"]), ["checks"])
        st = steps(self.ci, "checks")
        runs = [x.get("run", "") for x in st]
        uses = [x.get("uses", "") for x in st]
        self.assertTrue(any("git ls-files" in r for r in runs), "hygiene")
        self.assertTrue(any(u.startswith("gitleaks/gitleaks-action@") for u in uses), "gitleaks")
        self.assertIn("make gate", [r.strip() for r in runs], "gate 必须是 run: make gate")
        title = [x for x in st if "grep -Eq '^\\[(" in x.get("run", "")]
        self.assertEqual(len(title), 1, "标题检查")
        self.assertIn("gh pr view", title[0]["run"], "标题要从 API 读当前值，rerun 才能用上新标题")
        for x in st[2:]:   # checkout 之后的每一步：前一步失败也照跑，一次看到全部结果
            self.assertIn("!cancelled()", x.get("if", ""), x.get("name") or x.get("uses"))

    def test_ci_triggers(self):
        self.assertNotIn("edited", self.ci_on["pull_request"]["types"], "只改正文会产生 skipped 检查盖住红灯")
        self.assertNotIn("if", self.ci["jobs"]["checks"], "job 级 if 会产生同名 skipped 检查")
        self.assertIn("schedule", self.ci_on, "每周兜底跑 main")
        self.assertIn("pull-requests", self.ci["permissions"], "私有仓库 gitleaks 要读 PR 提交")

    def test_hygiene_fails_explicitly(self):
        # bash -e 不会因 `! cmd` 失败而退出：以 ! 开头的行不起作用
        hyg = [x["run"] for x in steps(self.ci, "checks") if "git ls-files" in x.get("run", "")][0]
        self.assertFalse([ln for ln in hyg.splitlines() if ln.strip().startswith("!")], "不要用 `! cmd`")
        self.assertIn('exit "$bad"', hyg)
        for needle in ("\\.env$", "data/", "\\.venv/"):
            self.assertIn(needle, hyg)

    def test_skipped_labels_do_not_cancel_review(self):
        # concurrency 先于 job if 生效：加别的标签必须进独立组（run_id），否则会取消正在跑的审查
        ai = self.ai["concurrency"]["group"]
        for needle in ("'labeled'", "label.name != 'ai-review'", "github.run_id"):
            self.assertIn(needle, ai)

    def test_ai_review_not_on_every_push(self):
        types = self.ai_on["pull_request"]["types"]
        self.assertNotIn("synchronize", types)
        self.assertIn("labeled", types)
        cond = self.ai["jobs"]["review"]["if"]
        self.assertIn("github.event.label.name == 'ai-review'", cond)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", cond, "fork PR 拿不到写权限，必须跳过")
        self.assertIn("!github.event.pull_request.draft", cond, "draft 打开时不审")

    def test_loader_accepts_list_form(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.yml"
            p.write_text("on: [pull_request, push]\njobs: {}\n", encoding="utf-8")
            self.assertEqual(set(load(p)[0]), {"pull_request", "push"})

if __name__ == "__main__":
    unittest.main()
