#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh_sessions_archive.py —— 把自动化 headless 会话加入 DSH 归档集（从分组中隐藏，历史保留）。

用法：
    python3 tools/dsh_sessions_archive.py --list     # 只列出将归档的会话
    python3 tools/dsh_sessions_archive.py --apply    # 备份后写入（DSH 重启后生效）

匹配依据：会话投影缓存里的标题命中自动化关键词。
注意：DSH 运行时可能覆盖 storage，执行后需重启 DSH 生效。
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

DSH = os.path.expanduser("~/.dsh")
STORAGE = os.path.join(DSH, "storages", "workspace.json")
CACHE = os.path.join(DSH, "storages", "session_projcache", "sessions")
KEYWORDS = ["自动巡检与修复", "政策文件解读", "AI 综合", "AI 分析", "周度", "月报", "季报", "年报", "加密货币政策"]


def titles():
    out = {}
    for f in glob.glob(os.path.join(CACHE, "*.json")):
        sid = os.path.basename(f)[:-5]
        try:
            rows = (json.load(open(f, encoding="utf-8")).get("record") or {}).get("rows") or {}
            t = (rows.get("title") or {}).get("val")
            if t:
                out[sid] = t
        except (ValueError, OSError):
            continue
    return out


def enc_dir(repo):
    return "--" + repo.strip("/").replace("/", "-") + "--"


def no_preset_sessions(repo):
    """无 agentPreset 事件的会话 = headless 一次性会话（Web 会话会记录 agent-preset 事件）。"""
    root = os.path.join(DSH, "sessions", enc_dir(repo))
    js = """
const fs=require('fs'), zlib=require('zlib'), path=require('path');
const root=process.argv[1]; const out=[];
for (const id of fs.readdirSync(root)) {
  const p=path.join(root,id,'session.v4.jsonl.zstd'); if(!fs.existsSync(p)) continue;
  try { const t=zlib.zstdDecompressSync(fs.readFileSync(p)).toString('utf8');
        if (!/agentPreset/.test(t)) out.push(id); } catch(e){}
}
console.log(JSON.stringify(out));
"""
    try:
        res = subprocess.run(["node", "-e", js, root], capture_output=True, text=True, timeout=120)
        return json.loads(res.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-preset", action="store_true", help="按“无 agentPreset 事件”识别 headless 会话")
    ap.add_argument("--repo", default=os.getcwd(), help="session cwd 对应的仓库目录")
    args = ap.parse_args(argv)
    data = json.load(open(STORAGE, encoding="utf-8"))
    state = data["global"]
    archived = list(state.get("archivedSessionIds") or [])
    ts = titles()
    if args.no_preset:
        cand = no_preset_sessions(args.repo)
        cand = {sid: ts.get(sid, "(无标题)") for sid in cand if sid not in archived}
    else:
        cand = {sid: t for sid, t in ts.items() if any(k in t for k in KEYWORDS) and sid not in archived}
    print("已归档 %d 个；本次待归档 %d 个：" % (len(archived), len(cand)))
    for sid, t in sorted(cand.items(), key=lambda x: x[1]):
        print("  - %-42s %s" % (t[:40], sid))
    if not args.apply:
        print("\n（--list 模式，未写入；加 --apply 执行）")
        return 0
    if not cand:
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(STORAGE, os.path.join(DSH, "backups", "workspace.json.bak-%s" % stamp))
    data["global"]["archivedSessionIds"] = archived + list(cand)
    tmp = STORAGE + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STORAGE)
    print("\n已写入归档集（+%d）；备份在 ~/.dsh/backups/workspace.json.bak-%s" % (len(cand), stamp))
    print("重启 DSH 后，这些会话不在分组里显示；点「显示已归档」可查看。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
