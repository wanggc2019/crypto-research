#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 DeepSeek 审查 PR diff，并把结果评论回 GitHub PR。"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MAX_DIFF = 60000


def get_diff(base, head):
    proc = subprocess.run(
        ["git", "diff", "--unified=40", "%s...%s" % (base, head)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:])
    return proc.stdout[:MAX_DIFF]


def review(diff):
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("DEEPSEEK_API_KEY 未配置，跳过 AI 审查")
        return ""
    prompt = """你是资深代码审查员。只审查下面 git diff 中的改动，不要臆测未出现的代码。
重点找：真实 bug、安全问题、边界条件、回归风险、测试缺失。
输出 Markdown：
## AI Code Review
### 严重
- `file:line` 问题（没有就写“无”）
### 建议
- `file:line` 问题（没有就写“无”）
### 结论
LGTM 或 需要修改

不要给完整重写代码，不要评论代码风格。只基于 diff。

DIFF:
```diff
%s
```""" % diff
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2000,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("DeepSeek HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300]))
    return data["choices"][0]["message"]["content"].strip()


def comment(body):
    repo = os.environ["REPO"]
    pr = os.environ["PR_NUMBER"]
    token = os.environ["GITHUB_TOKEN"]
    url = "https://api.github.com/repos/%s/issues/%s/comments" % (repo, pr)
    req = urllib.request.Request(
        url,
        data=json.dumps({"body": body}).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    if not os.environ.get("PR_NUMBER"):
        print("不是 PR 事件，跳过")
        return 0
    diff = get_diff(os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    if not diff.strip():
        print("diff 为空，跳过")
        return 0
    review_text = review(diff)
    if not review_text:
        return 0
    body = review_text + "\n\n> 由 DeepSeek 自动审查；仅供参考，不替代人工 review。"
    comment(body)
    print("AI 审查评论已发布")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("AI 审查失败：%s: %s" % (type(e).__name__, e))
        sys.exit(1)
