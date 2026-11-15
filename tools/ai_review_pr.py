#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 DeepSeek 审查 PR diff，并把结果评论回 GitHub PR。

安全边界：
- 只读 git diff，不执行 PR 代码；
- fork PR 直接跳过（GITHUB_TOKEN 只读，无法评论）；
- 同一 PR 的评论用 marker 复用，synchronize 不会重复刷评论；
- diff 按行截断并显式标记，避免把半个 hunk 发给模型。
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
GITHUB_API = "https://api.github.com"
MAX_DIFF = 40000
MAX_COMMENT = 60000
MARKER = "<!-- ai-review -->"


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("缺少环境变量 %s" % name)
    return value


def trim_diff(diff, limit=MAX_DIFF):
    """按行截断，保证不把半个 hunk 发给模型；截断时显式标记。"""
    if len(diff) <= limit:
        return diff
    lines = diff.splitlines(keepends=True)
    kept, used = [], 0
    for line in lines:
        if used + len(line) > limit:
            break
        kept.append(line)
        used += len(line)
    return "".join(kept).rstrip() + "\n[diff truncated: 仅审查以上部分，原始 diff %d 字符]\n" % len(diff)


def _run(cmd, timeout=60):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("命令超时：%s" % " ".join(cmd))
    if proc.returncode != 0:
        raise RuntimeError("%s 失败：%s" % (" ".join(cmd), (proc.stderr or "").strip()[-500:]))
    return proc.stdout


def _has_commit(sha):
    try:
        proc = subprocess.run(["git", "cat-file", "-e", "%s^{commit}" % sha],
                              capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def ensure_commit(sha, ref=None):
    """确保 base commit 在本地；fork PR/浅克隆时可能缺失。"""
    if _has_commit(sha):
        return
    try:
        _run(["git", "fetch", "--no-tags", "origin", sha], timeout=60)
    except RuntimeError:
        if ref:
            _run(["git", "fetch", "--no-tags", "origin", ref], timeout=60)
    if not _has_commit(sha):
        raise RuntimeError("base commit %s 不在本地仓库，且 fetch 后仍找不到" % sha)


def get_diff(base, head, base_ref=None):
    ensure_commit(base, base_ref)
    diff = _run(["git", "diff", "--unified=10", "%s...%s" % (base, head)], timeout=60)
    return trim_diff(diff)


def parse_review_response(data):
    """从 DeepSeek 响应中安全取出 content。"""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("DeepSeek 响应缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek 响应缺少 message.content")
    return content.strip()


def review(diff):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("::warning::DEEPSEEK_API_KEY 未配置，跳过 AI 审查")
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

DIFF_JSON（JSON 字符串，避免代码围栏被 diff 内容破坏）:
%s""" % json.dumps(diff, ensure_ascii=False)
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return parse_review_response(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise RuntimeError("DeepSeek HTTP %s: %s" % (
            e.code, e.read().decode("utf-8", "ignore")[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("DeepSeek 网络错误：%s" % e)
    except ValueError as e:
        raise RuntimeError("DeepSeek 响应不是合法 JSON：%s" % e)


def _github(method, path, token, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        GITHUB_API + path,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError("GitHub API %s %s -> HTTP %s: %s" % (
            method, path, e.code, e.read().decode("utf-8", "ignore")[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError("GitHub API 网络错误：%s" % e)


def select_existing_comment(comments):
    """找本 workflow 之前发的评论（带 MARKER 且作者是 Bot），返回最新一条。"""
    candidates = []
    for item in comments or []:
        if not isinstance(item, dict) or MARKER not in str(item.get("body") or ""):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        if user.get("type") == "Bot":
            candidates.append(item)
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("created_at") or ""))


def list_comments(token, repo, pr, max_pages=5):
    """分页取评论（GitHub issues comments 不支持 sort；只按 page 翻）。"""
    comments = []
    for page in range(1, max_pages + 1):
        _, batch = _github("GET", "/repos/%s/issues/%s/comments?per_page=100&page=%d"
                           % (repo, pr, page), token)
        if not isinstance(batch, list) or not batch:
            break
        comments.extend(batch)
        if len(batch) < 100:
            break
    return comments


def trim_comment(body, limit=MAX_COMMENT):
    """按行截断评论，避免截断 Markdown 代码块，并保留免责声明。"""
    if len(body) <= limit:
        return body
    tail = "\n\n[comment truncated: 审查结果过长]"
    kept, used = [], 0
    for line in body.splitlines(keepends=True):
        if used + len(line) > limit - len(tail):
            break
        kept.append(line)
        used += len(line)
    return "".join(kept).rstrip() + tail


def comment(body, token, repo, pr):
    existing = select_existing_comment(list_comments(token, repo, pr))
    if existing and existing.get("id"):
        try:
            _github("PATCH", "/repos/%s/issues/comments/%s" % (repo, existing["id"]),
                    token, {"body": body})
            return "updated"
        except RuntimeError:
            pass  # 评论被删/无权限时回退到新建
    _github("POST", "/repos/%s/issues/%s/comments" % (repo, pr), token, {"body": body})
    return "created"


def main():
    pr = os.environ.get("PR_NUMBER", "").strip()
    if not pr:
        print("不是 PR 事件，跳过")
        return 0
    repo = required_env("REPO")
    head_repo = os.environ.get("HEAD_REPO", "").strip()
    if head_repo and head_repo != repo:
        print("fork PR（%s != %s），跳过 AI 审查" % (head_repo, repo))
        return 0
    base = required_env("BASE_SHA")
    head = required_env("HEAD_SHA")
    diff = get_diff(base, head, os.environ.get("BASE_REF", "").strip() or None)
    if not diff.strip():
        print("diff 为空，跳过")
        return 0
    review_text = review(diff)
    if not review_text:
        return 0
    body = (MARKER + "\n" + review_text +
            "\n\n> 由 DeepSeek 自动审查；仅供参考，不替代人工 review。")
    action = comment(trim_comment(body), required_env("GITHUB_TOKEN"), repo, pr)
    print("AI 审查评论已%s" % ("更新" if action == "updated" else "发布"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("AI 审查失败：%s: %s" % (type(e).__name__, e))
        sys.exit(1)
