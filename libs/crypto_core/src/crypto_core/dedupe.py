# -*- coding: utf-8 -*-
"""去重与冷却状态。

- DailyKeys：按天分桶的去重键存储（兼容既有 notified.json 格式）
- Cooldown：分钟级冷却（价格类高频告警用）
"""
from . import store, timeutil


class DailyKeys:
    """`{"date": "YYYY-MM-DD", "keys": {key: ts}}` 的封装；跨天自动重置。"""

    def __init__(self, path, date=None):
        self.path = path
        self.date = date or timeutil.today_str()
        self._loaded = store.safe_json_load(path, {})
        self.keys = {}
        if isinstance(self._loaded, dict) and self._loaded.get("date") == self.date:
            self.keys = dict(self._loaded.get("keys") or {})

    def seen(self, key):
        return key in self.keys

    def mark(self, key, ts=None):
        self.keys[key] = ts or timeutil.ts_iso()

    def save(self):
        store.safe_json_dump(self.path, {"date": self.date, "keys": self.keys})
        return self


class Cooldown:
    """按 key 记录最近一次触发时间；`allow()` 在放行时顺便记录。"""

    def __init__(self, path, minutes=30):
        self.path = path
        self.minutes = seconds(minutes)
        self._loaded = store.safe_json_load(path, {})
        self.last = dict(self._loaded) if isinstance(self._loaded, dict) else {}

    def _now(self):
        import time

        return time.time()

    def allow(self, key, now_ts=None):
        now_ts = now_ts if now_ts is not None else self._now()
        last = float(self.last.get(key, 0) or 0)
        if last and now_ts - last < self.minutes:
            return False
        self.last[key] = now_ts
        return True

    def save(self):
        store.safe_json_dump(self.path, self.last)
        return self


def seconds(minutes):
    return float(minutes) * 60.0
