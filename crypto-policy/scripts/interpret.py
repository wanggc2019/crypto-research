#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interpret.py —— 为 P0/P1 生成「解读草稿」。

两种引擎：
- 规则抽取（无模型 key 时的默认/兜底）：原文要点 + 按主题给影响清单；
- DeepSeek（设置 DEEPSEEK_API_KEY 时）：调用 chat/completions 生成结构化解读。

产物：data/reports/interpret-<id>.md；状态：data/state/interpreted.json。
用法：
    python3 scripts/interpret.py --dry-run
    python3 scripts/interpret.py --notify
    python3 scripts/interpret.py --doc-id b4ea8f136ff2e48ea5919be3 --notify
"""
import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief  # noqa: E402
import classify  # noqa: E402
import index as policy_index  # noqa: E402

import notify  # noqa: E402
from common import (  # noqa: E402
    DATA_DIR,
    DOC_DIR,
    REPORT_DIR,
    STATE_DIR,
    ensure_dirs,
    safe_json_dump,
    safe_json_load,
    today_str,
    ts_iso,
)

PENDING_DIR = os.path.join(DATA_DIR, "pending")
DONE_DIR = os.path.join(PENDING_DIR, "done")
INTERPRETED_PATH = os.path.join(STATE_DIR, "interpreted.json")

IMPACT = [
    (("虚拟货币", "加密资产", "加密货币", "比特币", "以太币", "USDT", "泰达币", "crypto", "digital asset", "virtual asset"),
     "境内虚拟货币相关业务活动仍属非法金融活动；个人投资交易不受法律保护、损失自担。"),
    (("稳定币", "stablecoin"),
     "稳定币被纳入虚拟货币监管口径；未经同意不得在境外发行挂钩人民币的稳定币。"),
    (("RWA", "现实世界资产代币化", "tokenization", "tokenisation"),
     "境内 RWA 代币化及中介/技术服务被禁止（经同意的特定金融基础设施除外）。"),
    (("挖矿", "矿机", "mining"),
     "挖矿活动继续整治，新增项目与境内矿机销售被禁止。"),
    (("外汇", "forex"),
     "以虚拟货币为媒介的变相买卖外汇仍是外汇执法与刑事打击重点。"),
    (("洗钱", "非法经营", "money laundering"),
     "刑事风险集中在非法经营、洗钱、掩隐、帮信；资金涉赃会显著加重。"),
]


def _load_interpreted():
    data = safe_json_load(INTERPRETED_PATH, {"items": {}})
    if not isinstance(data, dict):
        data = {"items": {}}
    data.setdefault("items", {})
    return data


def _save_interpreted(data):
    safe_json_dump(INTERPRETED_PATH, data)


def _load_pending():
    if not os.path.isdir(PENDING_DIR):
        return []
    out = []
    for name in sorted(os.listdir(PENDING_DIR)):
        path = os.path.join(PENDING_DIR, name)
        if name.endswith(".json") and os.path.isfile(path):
            rec = safe_json_load(path, {})
            if rec:
                rec["_pending_path"] = path
                out.append(rec)
    out.sort(key=lambda r: {"P0": 0, "P1": 1}.get(r.get("level"), 9))
    return out


def _from_doc_id(doc_id):
    rec = safe_json_load(os.path.join(DOC_DIR, doc_id + ".json"), {})
    if not rec:
        return None
    rec = classify.build_record(rec)
    rec["supersedes"] = classify.supersedes_of(rec)
    return rec


def _impact_for(topics):
    out = []
    for keys, text in IMPACT:
        if any(k.lower() in (t or "").lower() for t in topics for k in keys):
            out.append(text)
    return out


def _rule_draft(rec):
    points = brief.pick_points(rec.get("body") or "", rec.get("topics") or [], limit=6)
    if not points and (rec.get("body") or ""):
        points = [(rec.get("body") or "").strip()[:240]]
    published = rec.get("published_at") or "—"
    effective = rec.get("effective_at") or ""
    if effective and effective > today_str():
        eff_note = "施行日 %s（尚未生效）" % effective
    elif effective:
        eff_note = "自 %s 起施行" % effective
    else:
        eff_note = "施行日期待核实"
    region = rec.get("jurisdiction") or "CN"
    region_txt = "" if region == "CN" else "[%s] " % region
    summary = "%s%s于 %s 发布《%s》（%s），类型为%s，%s。" % (
        region_txt,
        rec.get("issuer") or "有关部门",
        published,
        brief._short_title(rec.get("title") or "", 60),
        rec.get("doc_no") or "无文号",
        rec.get("doc_type_cn") or "文件",
        eff_note,
    )
    changes = []
    if rec.get("supersedes"):
        changes.append("本文件废止/替代：%s" % "、".join(rec["supersedes"]))
    if effective and effective > today_str():
        changes.append("尚未生效，注意过渡期")
    risks = [
        "本文为规则抽取草稿，未逐条核对，不构成法律意见",
        "具体适用需结合生效时间、配套细则与个案事实",
    ]
    if region != "CN":
        impact = [
            "%s 监管文件（%s）：适用范围、生效时间以原文为准。" % (region, rec.get("doc_type_cn") or "文件"),
            "对境内用户/中资机构：关注跨境展业、开户、托管、稳定币发行/储备、交易平台准入的合规要求。",
            "不直接改变中国境内规则；境内仍以中国监管文件为准。",
        ]
        changes.append("国外源无中国式文号/废止体系，按文件状态（拟议/最终/执法）跟踪")
        risks.append("国外规则不等同于中国法；跨境业务需逐辖区评估")
    else:
        impact = _impact_for(rec.get("topics") or [])
    return {
        "summary": summary,
        "key_points": points[:5],
        "changes": changes,
        "impact": impact,
        "risks": risks,
        "confidence": 0.5,
        "engine": "rule",
        "engine_label": "规则抽取",
    }


def _packet(rec, idx):
    old = []
    for no in rec.get("supersedes") or []:
        p = policy_index.find(idx, no)
        if p:
            old.append("%s《%s》状态=%s" % (no, p.get("title", ""), p.get("status")))
    return (
        "标题：%s\n机关：%s\n文号：%s\n类型：%s\n发布：%s\n施行：%s\n主题：%s\n旧规：%s\n\n原文：\n%s"
        % (
            rec.get("title") or "",
            rec.get("issuer") or "",
            rec.get("doc_no") or "",
            rec.get("doc_type_cn") or "",
            rec.get("published_at") or "",
            rec.get("effective_at") or "",
            "、".join(rec.get("topics") or []),
            "；".join(old) or "无",
            (rec.get("body") or "")[:12000],
        )
    )


def _llm_draft(rec, idx):
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    if not key:
        return None
    base = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "deepseek-chat"
    system = (
        "你是中国金融监管法律研究助手。只依据给定原文作答，不编造条款、案号、日期。"
        "输出严格 JSON：{summary,key_points:[],changes:[],impact:[],risks:[],confidence}。"
        "中文，简洁；summary 不超过 120 字；key_points 最多 5 条；confidence 0-1。"
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": _packet(rec, idx)},
        ],
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0) if m else text)
        out["engine"] = "deepseek"
        out["engine_label"] = "DeepSeek（%s）" % model
        out.setdefault("confidence", 0.6)
        return out
    except Exception as e:  # noqa: BLE001 - 模型失败一律回退规则
        print("⚠️ LLM 解读失败，回退规则抽取：%s" % e)
        return None


def _render_md(rec, draft):
    lines = [
        "# 解读草稿：%s" % (rec.get("title") or ""),
        "",
        "> 文件：%s｜类型：%s｜发布：%s｜施行：%s" % (
            rec.get("doc_no") or "—", rec.get("doc_type_cn") or "—",
            rec.get("published_at") or "—", rec.get("effective_at") or "—",
        ),
        "> 引擎：%s｜状态：草稿，非法律意见" % (draft.get("engine_label") or "规则抽取"),
        "",
        "## 一句话",
        "",
        draft.get("summary") or "",
        "",
        "## 原文要点",
        "",
    ]
    for p in draft.get("key_points") or []:
        lines.append("- %s" % p)
    lines += ["", "## 与旧规的关系", ""]
    for c in draft.get("changes") or ["未识别到废止/替代关系"]:
        lines.append("- %s" % c)
    lines += ["", "## 影响", ""]
    for x in draft.get("impact") or ["未匹配到主题影响模板，请人工判断"]:
        lines.append("- %s" % x)
    lines += ["", "## 风险提示", ""]
    for r in draft.get("risks") or []:
        lines.append("- %s" % r)
    lines += ["", "## 原文", "", rec.get("url") or "", ""]
    return "\n".join(lines)


def _archive(rec):
    path = rec.get("_pending_path")
    if path and os.path.isfile(path):
        os.makedirs(DONE_DIR, exist_ok=True)
        os.replace(path, os.path.join(DONE_DIR, os.path.basename(path)))


def run(args):
    ensure_dirs()
    idx = policy_index.load()
    interpreted = _load_interpreted()

    if args.doc_id:
        records = []
        for doc_id in [x.strip() for x in args.doc_id.split(",") if x.strip()]:
            rec = _from_doc_id(doc_id)
            if rec:
                records.append(rec)
            else:
                print("⚠️ 找不到文档：%s" % doc_id)
    else:
        records = _load_pending()

    todo = []
    for rec in records:
        if not args.force and (interpreted["items"].get(rec.get("id")) or {}).get("status") == "pushed":
            continue
        todo.append(rec)
    todo = todo[: args.limit]

    if not todo:
        print("pending 为空或都已解读（%s）" % ("dry-run" if args.dry_run else "无新任务"))
        return 0

    sent = 0
    for rec in todo:
        draft = None
        if not args.no_llm:
            draft = _llm_draft(rec, idx)
        if not draft:
            draft = _rule_draft(rec)
        md = _render_md(rec, draft)
        report_path = os.path.join(REPORT_DIR, "interpret-%s.md" % rec["id"])
        if args.dry_run:
            print("=" * 60)
            print(md)
            continue
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(md)
        status = "drafted"
        if args.notify:
            ok, err = notify.send(brief.render_interpret_card(rec, draft))
            if ok:
                sent += 1
                status = "pushed"
                _archive(rec)
            else:
                print("❌ 解读推送失败：%s" % err)
        interpreted["items"][rec["id"]] = {
            "engine": draft.get("engine"),
            "status": status,
            "report": report_path,
            "ts": ts_iso(),
        }
        _save_interpreted(interpreted)
        print("%s %s（%s）→ %s" % ("✅" if status == "pushed" else "📝", rec.get("doc_no") or rec["id"],
                                   draft.get("engine_label"), os.path.basename(report_path)))

    print("解读完成：处理 %d 条，推送 %d 条" % (len(todo), sent))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-policy 解读草稿")
    parser.add_argument("--doc-id", help="指定文档 id（逗号分隔）；默认处理 data/pending/")
    parser.add_argument("--limit", type=int, default=3, help="单次最多处理条数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘、不推送")
    parser.add_argument("--notify", action="store_true", help="推送解读草稿卡片")
    parser.add_argument("--no-llm", action="store_true", help="强制规则抽取，不调用模型")
    parser.add_argument("--force", action="store_true", help="已推送的也重新生成")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as e:  # noqa: BLE001
        print("❌ 解读失败：%s: %s" % (type(e).__name__, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
