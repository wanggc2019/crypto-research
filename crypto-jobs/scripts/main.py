#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— crypto-jobs 入口。

两种模式：
  日更（默认）：拉取所有源 → 归一化 + 技能抽取 → 存快照/索引 → 差分 → 日报/导出（不推送）
  周报（--weekly）：读历史快照 → 技能趋势周报 → 落盘 + 可选 Telegram 推送
  速报（--alerts）：只抓取并推「上次之后新增的目标岗位」，不写快照/报告

用法：
    python3 scripts/main.py                          # 跑一轮，不推送
    python3 scripts/main.py --dry-run                # 联网抓取，只打印，不写盘不推送
    python3 scripts/main.py --fixtures tests/fixtures/sources   # 离线回放（CI 用）
    python3 scripts/main.py --weekly --notify        # 生成并推送周报
    python3 scripts/main.py --test                   # Telegram 通道测试

退出码：0 成功 / 1 部分源失败但有产出 / 2 配置或凭据错误 / 3 网络全挂 / 4 锁被占用跳过。
"""
import argparse
import datetime
import json
import os
import sys

import metrics
import normalize
import report
import skills
import snapshot
import sources

import common
import notify

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_NETWORK = 3


def _now_iso():
    return common.ts_iso()


def _log(data_dir, line):
    try:
        common.ensure_dirs()
        with open(os.path.join(data_dir, "logs", "crypto-jobs.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (_now_iso(), line))
    except OSError:
        pass


ALERT_SEEN_SCHEMA = "job-alerts/v1"
ALERT_MAX_SEEN = 20000


def _alert_seen_path(data_dir):
    return os.path.join(data_dir, "state", "alert-seen.json")


def load_alert_seen(data_dir):
    data = common.safe_json_load(_alert_seen_path(data_dir), None)
    if isinstance(data, dict) and isinstance(data.get("seen"), list):
        return data
    return {"schema": ALERT_SEEN_SCHEMA, "updated_at": "", "seen": []}


def save_alert_seen(data_dir, keys):
    common.safe_json_dump(_alert_seen_path(data_dir), {
        "schema": ALERT_SEEN_SCHEMA,
        "updated_at": common.ts_iso(),
        "seen": sorted({str(key) for key in (keys or [])})[:ALERT_MAX_SEEN],
    })


def select_new_jobs(jobs, seen_keys):
    """返回从未见过的岗位（按 key 去重）。"""
    seen = set(seen_keys or [])
    return [job for job in (jobs or []) if job.get("key") not in seen]


def should_send_daily(cfg, new_jobs, manual=False):
    """日报是否推送：手动 --daily-digest 总是推；自动模式只看配置阈值，不做静默时段限制。"""
    if manual:
        return True
    delivery = (cfg or {}).get("delivery") or {}
    if not delivery.get("daily_digest"):
        return False
    return int(new_jobs or 0) >= int(delivery.get("daily_min_new_jobs") or 0)


def weekly_due(cfg, date=None):
    """今天是否周报日（按 config/app.yaml delivery.weekly.weekday，ISO 周几 1-7）。"""
    weekly = ((cfg or {}).get("delivery") or {}).get("weekly") or {}
    try:
        weekday = int(weekly.get("weekday") or 1)
    except (TypeError, ValueError):
        weekday = 1
    day = str(date or common.today_str())[:10]
    try:
        return datetime.date.fromisoformat(day).isoweekday() == weekday
    except ValueError:
        return False


def _load_inputs():
    cfg = common.load_config("app.yaml")
    if not cfg:
        raise SystemExit("缺少 config/app.yaml")
    if cfg.get("schema_version") != 1:
        raise SystemExit("config/app.yaml 的 schema_version 必须为 1")
    if not cfg.get("enabled", True):
        raise SystemExit("config/app.yaml enabled=false，已停用")
    roles_cfg = common.load_config("roles.yaml")
    taxonomy = skills.load()
    return cfg, roles_cfg, taxonomy


def _fetch(args):
    kwargs = {"limit": args.limit, "fixtures_dir": args.fixtures}
    if args.only:
        kwargs["only"] = [s.strip() for s in args.only.split(",") if s.strip()]
    return sources.fetch_all(**kwargs)


def _write_raw(data_dir, date, raw_jobs, health, target_keys=None):
    """写原始目标岗位（含 JD 正文，供换词典重算）；非目标岗位只计数不落盘，控制数据量。"""
    folder = os.path.join(data_dir, "raw", str(date))
    os.makedirs(folder, exist_ok=True)
    kept = 0
    with open(os.path.join(folder, "jobs.jsonl"), "w", encoding="utf-8") as f:
        for job in raw_jobs:
            if target_keys is not None and normalize.job_key(job) not in target_keys:
                continue
            f.write(json.dumps(job, ensure_ascii=False) + "\n")
            kept += 1
    common.safe_json_dump(os.path.join(folder, "source-health.json"), health)
    return kept


def _export_latest(data_dir, data, jobs, health):
    """写跨模块只读契约 exports/*.json（schema + generated_at + source_module）。"""
    export_dir = os.path.join(data_dir, "exports")
    common.safe_json_dump(os.path.join(export_dir, "skill-report.json"), data)
    common.safe_json_dump(os.path.join(export_dir, "company-stats.json"), {
        "schema": "company-stats/v1",
        "generated_at": data.get("generated_at"),
        "source_module": "crypto-jobs",
        "date": data.get("date"),
        "counts": {"jobs": (data.get("overview") or {}).get("jobs", 0),
                   "companies": (data.get("overview") or {}).get("companies", 0)},
        "companies": data.get("companies") or [],
    })
    common.safe_json_dump(os.path.join(export_dir, "jobs-latest.json"), {
        "schema": "jobs-export/v1",
        "generated_at": data.get("generated_at"),
        "source_module": "crypto-jobs",
        "date": data.get("date"),
        "counts": data.get("overview"),
        "source_health": health,
        "jobs": [{
            "key": j.get("key"),
            "company": j.get("company"),
            "title": j.get("title"),
            "role": j.get("role"),
            "seniority": j.get("seniority"),
            "remote": j.get("remote"),
            "locations": j.get("locations"),
            "url": j.get("url"),
            "skills": j.get("skills"),
        } for j in jobs],
    })


def _write_report(data_dir, name, text):
    path = os.path.join(data_dir, "reports", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _last_run(data_dir, payload):
    common.safe_json_dump(os.path.join(data_dir, "state", "last_run.json"), payload)


def _summary(data, rc, stats=None, health=None):
    health = health or data.get("source_health") or []
    ok = sum(1 for h in health if h.get("ok"))
    return {
        "date": data.get("date"),
        "rc": rc,
        "overview": data.get("overview"),
        "counts": stats or {},
        "sources": {"ok": ok, "total": len(health)},
        "top_skills": [{"skill": r["skill"], "label": r["label"], "count": r["count"],
                        "delta": r.get("delta")}
                       for r in (data.get("top_skills") or [])[:10]],
        "period": data.get("period"),
    }


def _print_summary(summary):
    ov = summary.get("overview") or {}
    print("[crypto-jobs] %s 在招 %d｜新增 %d｜关闭 %d｜源 %d/%d" % (
        summary.get("date"), ov.get("jobs", 0), ov.get("new", 0), ov.get("closed", 0),
        summary.get("sources", {}).get("ok", 0), summary.get("sources", {}).get("total", 0)))
    for row in summary.get("top_skills") or []:
        delta = row.get("delta")
        delta_text = "—" if delta is None else "%+d" % delta
        print("  %-22s %4d (%s)" % (row.get("label"), row.get("count", 0), delta_text))


def _rc_from_health(health, jobs):
    if not health:
        return EXIT_OK if jobs else EXIT_NETWORK
    ok = sum(1 for h in health if h.get("ok"))
    if ok == 0:
        return EXIT_NETWORK
    if ok < len(health):
        return EXIT_PARTIAL
    return EXIT_OK


def run_daily(args):
    started_at = _now_iso()
    try:
        cfg, roles_cfg, taxonomy = _load_inputs()
    except SystemExit as exc:
        print("❌ %s" % exc)
        return EXIT_CONFIG

    exchanges_cfg = common.load_config("exchanges.yaml")
    date = args.date or common.today_str()
    data_dir = os.path.abspath(args.data_dir or common.DATA_DIR)
    dry = bool(args.dry_run)

    try:
        raw_jobs, health = _fetch(args)
    except Exception as exc:  # noqa: BLE001 - 采集层异常统一降级
        print("❌ 采集失败：%s" % exc)
        _log(data_dir, "collect failed: %s" % exc)
        return EXIT_NETWORK

    jobs, stats = normalize.normalize_jobs(raw_jobs, cfg, roles_cfg, taxonomy)
    rc = _rc_from_health(health, raw_jobs)

    prev_date, prev = snapshot.load_previous(data_dir, date)
    index = snapshot.load_index(data_dir)
    snap = snapshot.build_snapshot(date, jobs, health, stats)
    diff = snapshot.diff_jobs((prev or {}).get("jobs"), jobs, health)

    # 日报指标：对比上一份快照
    data = metrics.build_report_data(
        current_snapshot=snap, baseline_snapshot=prev, index=index,
        cfg=cfg, taxonomy=taxonomy, date=date, period_days=1,
        exchanges=exchanges_cfg)
    if prev is None:
        # 首次建基线：日报不把全部岗位标成"新增"
        data["overview"].update({"new": 0, "closed": 0, "changed": 0})
    else:
        data["overview"]["new"] = len(diff["new_keys"])
        data["overview"]["closed"] = len(diff["closed_keys"])
        data["overview"]["changed"] = len(diff["changed"])
    daily_md = report.render_daily(data, cfg)

    summary = _summary(data, rc, stats, health)
    if dry:
        print(daily_md)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        _print_summary(summary)
        return rc

    common.ensure_dirs()
    _write_raw(data_dir, date, raw_jobs, health, target_keys={j["key"] for j in jobs})
    snapshot.update_index(index, jobs, date)
    snapshot.prune_snapshots(data_dir, int((cfg.get("snapshot") or {}).get("keep_days") or 400))
    report_path = _write_report(data_dir, "%s.md" % date, daily_md)
    snapshot.save_snapshot(data_dir, snap)
    snapshot.save_index(data_dir, index)
    _export_latest(data_dir, data, jobs, health)

    finished_at = _now_iso()
    _last_run(data_dir, {
        "started_at": started_at,
        "finished_at": finished_at,
        "rc": rc,
        "mode": "daily",
        "counts": stats,
        "overview": data["overview"],
        "source_health": health,
        "report": report_path,
    })
    _log(data_dir, "daily rc=%s fetched=%s target=%s new=%s closed=%s sources=%s/%s" % (
        rc, stats.get("fetched"), stats.get("target"), len(diff["new_keys"]),
        len(diff["closed_keys"]), sum(1 for h in health if h.get("ok")), len(health)))

    # 日报推送：默认关闭。--daily-digest 手动补发，或配置开启后按阈值自动推（不做静默时段限制）
    want_daily = should_send_daily(cfg, len(diff["new_keys"]), manual=args.daily_digest)
    if args.notify and want_daily:
        if snapshot.was_pushed(data_dir, "daily", date) and not args.force:
            print("⏭ 该日期日报已推送过，跳过重发（需要重发用 --force）")
        else:
            ok, err = notify.send(report.render_daily_telegram(data))
            if ok:
                snapshot.mark_pushed(data_dir, "daily", date, True, "daily digest")
                print("✅ 日报已推送")
            else:
                print("⚠️ Telegram 推送失败：%s" % err)

    if not args.json:
        _print_summary(summary)
        print("→ 日报：%s" % report_path)
    else:
        print(json.dumps(dict(summary, report=report_path), ensure_ascii=False, indent=1))
    return rc


def weekly_baseline(data_dir, cur_date):
    """选择周报 baseline：优先 7 天前；历史不足 7 天时用最早的一份旧快照。"""
    target = (datetime.datetime.strptime(str(cur_date)[:10], "%Y-%m-%d").date()
              - datetime.timedelta(days=7)).isoformat()
    base_date, base = snapshot.load_on_or_before(data_dir, target)
    if not base:
        earlier = [day for day in snapshot.list_snapshot_dates(data_dir)
                   if str(day) < str(cur_date)]
        if earlier:
            base_date = earlier[0]
            base = snapshot.load_snapshot(data_dir, base_date)
    if base_date and str(base_date) >= str(cur_date):
        return None, None
    return base_date, base


def run_alerts(args):
    """事件速报：抓取 → 只推「上次之后新增的目标岗位」，不写快照/日报。"""
    started = _now_iso()
    try:
        cfg, roles_cfg, taxonomy = _load_inputs()
    except SystemExit as exc:
        print("❌ %s" % exc)
        return EXIT_CONFIG
    alerts_cfg = cfg.get("alerts") or {}
    if not alerts_cfg.get("enabled", True):
        print("⏭ alerts 已关闭")
        return EXIT_OK
    data_dir = os.path.abspath(args.data_dir or common.DATA_DIR)
    try:
        raw_jobs, health = _fetch(args)
    except Exception as exc:  # noqa: BLE001
        print("❌ 采集失败：%s" % exc)
        _log(data_dir, "alerts collect failed: %s" % exc)
        return EXIT_NETWORK

    jobs, _stats = normalize.normalize_jobs(raw_jobs, cfg, roles_cfg, taxonomy)
    current_keys = {job["key"] for job in jobs}
    rc = _rc_from_health(health, raw_jobs)

    seen_data = load_alert_seen(data_dir)
    seen = set(seen_data.get("seen") or [])
    if not seen:
        if not args.dry_run:
            save_alert_seen(data_dir, current_keys)
        print("首次建立新岗位基线：记录 %d 个岗位，不推送" % len(current_keys))
        return rc

    new_jobs = select_new_jobs(jobs, seen)
    min_new = int(alerts_cfg.get("min_new") or 1)
    if len(new_jobs) < min_new:
        if not args.dry_run:
            save_alert_seen(data_dir, seen | current_keys)
        print("新增 %d 个 < 阈值 %d，本轮不推送" % (len(new_jobs), min_new))
        return rc

    max_items = int(alerts_cfg.get("max_items") or 8)
    text = report.render_job_alerts(new_jobs, _alert_type_map(), max_items,
                                    now_text=common.ts_iso())
    if args.dry_run:
        print(text)
        return rc
    if not args.notify:
        print("未开启 --notify：发现 %d 个新岗位，但不推送、不更新已见集合" % len(new_jobs))
        return rc

    ok, err = notify.send(text)
    if not ok:
        print("⚠️ 速报推送失败：%s（下轮重试）" % err)
        _log(data_dir, "alerts push failed: %s" % err)
        return rc if rc != EXIT_OK else EXIT_PARTIAL
    save_alert_seen(data_dir, seen | current_keys)
    _log(data_dir, "alerts pushed new=%d listed=%d started=%s" % (
        len(new_jobs), min(len(new_jobs), max_items), started))
    print("✅ 新岗位速报：%d 个（已推送 独立 Bot（.env 配置））" % len(new_jobs))
    return rc


def _alert_type_map():
    cfg = common.load_config("exchanges.yaml") or {}
    mapping = dict(cfg.get("company_types") or {})
    for group, label in (("cex", "CEX"), ("dex", "DEX")):
        for entry in cfg.get(group) or []:
            slug = entry.get("slug")
            if slug:
                mapping[slug] = label
    return mapping


def run_weekly(args, due_only=False):
    started_at = _now_iso()
    try:
        cfg, _roles_cfg, taxonomy = _load_inputs()
    except SystemExit as exc:
        print("❌ %s" % exc)
        return EXIT_CONFIG

    exchanges_cfg = common.load_config("exchanges.yaml")
    date = args.date or common.today_str()
    if due_only and not weekly_due(cfg, date):
        print("⏭ %s 不是周报日（delivery.weekly.weekday=%s），跳过周报" % (
            date, ((cfg.get("delivery") or {}).get("weekly") or {}).get("weekday", 1)))
        return EXIT_OK

    data_dir = os.path.abspath(args.data_dir or common.DATA_DIR)
    cur_date, cur = snapshot.load_on_or_before(data_dir, date)
    if not cur:
        print("❌ 没有可用快照（先跑一次日更：./scripts/run.sh）")
        return EXIT_CONFIG

    cur_date_s = str(cur_date)
    base_date, base = weekly_baseline(data_dir, cur_date_s)
    index = snapshot.load_index(data_dir)
    history = snapshot.load_history(data_dir, cur_date_s, weeks=6)
    data = metrics.build_report_data(cur, base, index, cfg, taxonomy, date=cur_date_s,
                                     period_days=7, history_snapshots=history,
                                     exchanges=exchanges_cfg)
    md = report.render_weekly(data, cfg)

    if args.dry_run:
        print(md)
        if args.json:
            print(json.dumps(_summary(data, EXIT_OK), ensure_ascii=False, indent=1))
        return EXIT_OK

    common.ensure_dirs()
    report_path = _write_report(data_dir, "%s-weekly.md" % cur_date_s, md)
    _export_latest(data_dir, data, cur.get("jobs") or [], cur.get("source_health") or [])
    delivered = None
    if args.notify and snapshot.was_pushed(data_dir, "weekly", cur_date_s) and not args.force:
        delivered = {"channel": "telegram", "ok": True, "skipped_duplicate": True}
        print("⏭ 该日期周报已推送过，跳过重发（需要重发用 --force）")
    elif args.notify:
        ok, err = notify.send_weekly(data, report_path=report_path)
        delivered = {"channel": "telegram", "ok": ok, "error": err,
                     "digest_and_document": True}
        if ok:
            snapshot.mark_pushed(data_dir, "weekly", cur_date_s, True, "digest+document")
            print("✅ 周报已推送（摘要 + 完整 Markdown 报告）")
        else:
            print("⚠️ Telegram 推送失败：%s" % err)

    _last_run(data_dir, {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "rc": EXIT_OK,
        "mode": "weekly",
        "counts": cur.get("counts") or {},
        "overview": data["overview"],
        "source_health": cur.get("source_health") or [],
        "report": report_path,
        "delivered": delivered,
    })
    _log(data_dir, "weekly date=%s jobs=%s new=%s closed=%s rising=%s delivered=%s" % (
        cur_date_s, data["overview"].get("jobs"), data["overview"].get("new"),
        data["overview"].get("closed"), len(data.get("rising") or []),
        (delivered or {}).get("ok")))
    if not args.json:
        _print_summary(_summary(data, EXIT_OK))
        print("→ 周报：%s" % report_path)
    else:
        print(json.dumps(dict(_summary(data, EXIT_OK), report=report_path),
                         ensure_ascii=False, indent=1))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="crypto-jobs：加密行业技术岗位市场情报")
    parser.add_argument("--dry-run", action="store_true", help="抓取/计算但只打印，不写盘不推送")
    parser.add_argument("--notify", action="store_true", help="生成后推送 Telegram（按配置）")
    parser.add_argument("--force", action="store_true", help="忽略「同日已推送」去重，强制重发")
    parser.add_argument("--alerts", action="store_true",
                        help="新岗位速报模式：抓取并只推新增目标岗位（不写快照/日报）")
    parser.add_argument("--weekly", action="store_true", help="周报模式：只读历史快照，输出技能趋势")
    parser.add_argument("--weekly-if-due", action="store_true",
                        help="单任务模式：只有今天是配置的周报日才生成/推送周报（否则静默退出 0）")
    parser.add_argument("--daily-digest", action="store_true",
                        help="手动补发日报摘要（忽略 daily_digest 开关；仍受同日去重约束）")
    parser.add_argument("--fixtures", help="离线回放目录（CI 用，禁止联网）")
    parser.add_argument("--data-dir", help="覆盖 data 目录（测试/CI 用）")
    parser.add_argument("--date", help="处理日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--limit", type=int, default=None, help="单源最多取多少条（调试）")
    parser.add_argument("--only", help="只跑指定 source_id，逗号分隔")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--test", action="store_true", help="发送 Telegram 测试卡片")
    args = parser.parse_args(argv)

    if args.test:
        ok, err = notify.send(report.render_test_card(), dry_run=args.dry_run, silent=True)
        if ok:
            print("✅ 通道正常" if not args.dry_run else "✅ dry-run 通过")
            return EXIT_OK
        print("❌ 发送失败：%s" % err)
        return EXIT_CONFIG
    if args.alerts:
        return run_alerts(args)
    if args.weekly_if_due:
        return run_weekly(args, due_only=True)
    if args.weekly:
        return run_weekly(args)
    return run_daily(args)


if __name__ == "__main__":
    sys.exit(main())
