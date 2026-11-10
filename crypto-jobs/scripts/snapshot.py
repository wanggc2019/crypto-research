#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snapshot.py —— 每日岗位快照、历史索引、差分与保留清理。

数据契约：
- state/snapshots/<YYYY-MM-DD>.json  jobs-snapshot/v1（当天在招的目标岗位 + 技能 + 源健康）
- state/jobs-index.json             jobs-index/v1（first_seen / last_seen，支撑"本周新增/关闭"）
- raw/<date>/*.jsonl                原始数据（由 main.py 写，保留交给 tools/prune.py）

设计要点：快照只存"归一化后不带 JD 正文"的记录（体积可控）；JD 正文在 raw 里，
需要复算技能时从 raw 回放即可。
"""
import datetime
import os

import common

SNAPSHOT_SCHEMA = "jobs-snapshot/v1"
INDEX_SCHEMA = "jobs-index/v1"


def _as_date(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def snapshots_dir(data_dir):
    return os.path.join(data_dir, "state", "snapshots")


def snapshot_path(data_dir, date):
    return os.path.join(snapshots_dir(data_dir), "%s.json" % str(date)[:10])


def build_snapshot(date, jobs, health, stats, generated_at=None, extra=None):
    snap = {
        "schema": SNAPSHOT_SCHEMA,
        "date": str(date)[:10],
        "generated_at": generated_at or common.ts_iso(),
        "source_health": list(health or []),
        "counts": dict(stats or {}),
        "jobs": list(jobs or []),
    }
    if extra:
        snap.update(extra)
    return snap


def save_snapshot(data_dir, snap):
    path = snapshot_path(data_dir, snap["date"])
    common.safe_json_dump(path, snap)
    return path


def load_snapshot(data_dir, date):
    path = snapshot_path(data_dir, date)
    data = common.safe_json_load(path, None)
    if isinstance(data, dict) and data.get("schema") == SNAPSHOT_SCHEMA:
        return data
    return None


def list_snapshot_dates(data_dir):
    folder = snapshots_dir(data_dir)
    if not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            out.append(_as_date(name[:-5]))
        except ValueError:
            continue
    return sorted(out)


def load_on_or_before(data_dir, date):
    """<= date 的最近一份快照，返回 (date, snap) 或 (None, None)。"""
    limit = _as_date(date)
    for day in reversed(list_snapshot_dates(data_dir)):
        if day <= limit:
            snap = load_snapshot(data_dir, day)
            if snap:
                return day, snap
    return None, None


def load_previous(data_dir, date):
    """严格早于 date 的最近一份快照。"""
    limit = _as_date(date)
    for day in reversed(list_snapshot_dates(data_dir)):
        if day < limit:
            snap = load_snapshot(data_dir, day)
            if snap:
                return day, snap
    return None, None


def load_history(data_dir, date, weeks=6, step_days=7):
    """取 <= date、按 step_days 间隔的至多 weeks 份快照（用于技能趋势表）。"""
    limit = _as_date(date)
    wanted = [limit - datetime.timedelta(days=step_days * i) for i in range(weeks)]
    out = []
    for target in sorted(wanted):
        day, snap = load_on_or_before(data_dir, target)
        if snap and all(day != d for d, _ in out):
            out.append((day, snap))
    return out


def prune_snapshots(data_dir, keep_days, today=None):
    """删除超过保留期的快照，返回删除数量。"""
    cutoff = _as_date(today or common.today_str()) - datetime.timedelta(days=int(keep_days))
    removed = 0
    for day in list_snapshot_dates(data_dir):
        if day < cutoff:
            try:
                os.remove(snapshot_path(data_dir, day))
                removed += 1
            except OSError:
                pass
    return removed


def load_index(data_dir):
    path = os.path.join(data_dir, "state", "jobs-index.json")
    data = common.safe_json_load(path, None)
    if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
        data.setdefault("schema", INDEX_SCHEMA)
        return data
    return {"schema": INDEX_SCHEMA, "updated_at": "", "jobs": {}}


def save_index(data_dir, index):
    index["schema"] = INDEX_SCHEMA
    index["updated_at"] = common.ts_iso()
    common.safe_json_dump(os.path.join(data_dir, "state", "jobs-index.json"), index)
    return index


def update_index(index, jobs, date):
    """更新 first_seen / last_seen；返回 index（原地修改）。"""
    day = str(date)[:10]
    for job in jobs or []:
        key = job.get("key") or ""
        if not key:
            continue
        entry = index["jobs"].setdefault(key, {
            "first_seen": day,
            "company": job.get("company") or "",
            "company_slug": job.get("company_slug") or "",
            "title": job.get("title") or "",
            "role": job.get("role") or "",
            "source_id": job.get("source_id") or "",
        })
        entry["first_seen"] = min(entry.get("first_seen") or day, day)
        entry["last_seen"] = day
        entry["company"] = job.get("company") or entry.get("company") or ""
        entry["company_slug"] = job.get("company_slug") or entry.get("company_slug") or ""
        entry["title"] = job.get("title") or entry.get("title") or ""
        entry["role"] = job.get("role") or entry.get("role") or ""
        entry["source_id"] = job.get("source_id") or entry.get("source_id") or ""
    index["jobs"] = dict(sorted(index["jobs"].items()))
    return index


def index_new_jobs(index, date, days=7):
    """first_seen 落在 [date-days+1, date] 的岗位 key 集合。"""
    end = _as_date(date)
    start = end - datetime.timedelta(days=int(days) - 1)
    out = set()
    for key, entry in (index or {}).get("jobs", {}).items():
        try:
            first = _as_date(entry.get("first_seen"))
        except (TypeError, ValueError):
            continue
        if start <= first <= end:
            out.add(key)
    return out


def _job_map(jobs):
    return {j.get("key"): j for j in (jobs or []) if j.get("key")}


def diff_jobs(prev_jobs, cur_jobs, health=None):
    """对比两份快照的岗位集合。

    closed 只在「该岗位所属源在本轮是健康的」时才计入，避免某源临时失败造成假关闭。
    """
    prev = _job_map(prev_jobs)
    cur = _job_map(cur_jobs)
    healthy = None
    if health is not None:
        healthy = {h.get("source_id") for h in health if h.get("ok")}

    new_keys = sorted(set(cur) - set(prev))
    closed_keys = []
    for key in sorted(set(prev) - set(cur)):
        if healthy is not None and prev[key].get("source_id") not in healthy:
            continue
        closed_keys.append(key)
    changed = []
    for key in sorted(set(prev) & set(cur)):
        a, b = prev[key], cur[key]
        fields = []
        if a.get("description_hash") != b.get("description_hash"):
            fields.append("description")
        if common.norm_title(a.get("title") or "") != common.norm_title(b.get("title") or ""):
            fields.append("title")
        if a.get("skills") != b.get("skills"):
            fields.append("skills")
        if fields:
            changed.append({"key": key, "fields": fields})
    return {
        "new_keys": new_keys,
        "closed_keys": closed_keys,
        "changed": changed,
        "new_jobs": [cur[k] for k in new_keys],
        "closed_jobs": [prev[k] for k in closed_keys],
    }


PUSH_SCHEMA = "push-log/v1"


def _push_log_path(data_dir):
    return os.path.join(data_dir, "state", "push-log.json")


def load_push_log(data_dir):
    data = common.safe_json_load(_push_log_path(data_dir), None)
    if isinstance(data, dict) and isinstance(data.get("sent"), dict):
        data.setdefault("schema", PUSH_SCHEMA)
        return data
    return {"schema": PUSH_SCHEMA, "sent": {}}


def was_pushed(data_dir, kind, date):
    """该类型（weekly/daily）该日期是否已成功推送过。"""
    entry = load_push_log(data_dir)["sent"].get("%s:%s" % (kind, str(date)[:10]))
    return bool(entry and entry.get("ok"))


def mark_pushed(data_dir, kind, date, ok=True, detail=""):
    log = load_push_log(data_dir)
    log["sent"]["%s:%s" % (kind, str(date)[:10])] = {
        "ts": common.ts_iso(), "ok": bool(ok), "detail": detail,
    }
    common.safe_json_dump(_push_log_path(data_dir), log)
    return log


def jobs_by_key(jobs):
    return _job_map(jobs)
