# -*- coding: utf-8 -*-
"""跨进程共享的滑动窗口限速（文件锁 + 时间戳列表），用于多个 launchd 任务共用同一外部配额。

典型：r.jina.ai 匿名 20 RPM 是按出口 IP 计的，crypto-news / crypto-policy / crypto-x-kol
各自节流也会叠加超限（→ 429 / AbuseAlleviationError）。这里让所有进程排同一个队。

状态：<repo>/data/state/ratelimit-<name>.json（可用 CRYPTO_RATELIMIT_DIR 覆盖；CRYPTO_RATELIMIT=0 关闭）。
"""
import fcntl
import json
import os
import time

from . import paths

WINDOW = 60.0


class RateLimited(OSError):
    """在 max_wait 内拿不到配额。消息含 "Too Many Requests"，让既有的「网页限制 → SKIP」判断自然生效。"""


def enabled():
    return os.environ.get("CRYPTO_RATELIMIT", "1") != "0"


def state_dir():
    return os.environ.get("CRYPTO_RATELIMIT_DIR") or str(paths.repo_root() / "data" / "state")


def acquire(name, per_minute, max_wait=90.0, directory=None, clock=time.time, sleep=time.sleep):
    """占用 name 的一个配额；窗口已满则等待最早一条过期。返回实际等待秒数；超过 max_wait 抛 RateLimited。"""
    per_minute = max(1, int(per_minute))
    directory = directory or state_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "ratelimit-%s.json" % name)
    started = clock()
    while True:
        with open(path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            now = clock()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    stamps = [float(t) for t in json.load(f)]
            except (OSError, ValueError, TypeError):
                stamps = []
            # 时钟回拨时「未来」时间戳钳到 now（继续计入窗口），而不是丢弃导致窗口被清空、短时超发
            stamps = sorted(min(t, now) for t in stamps if t > now - WINDOW)
            if len(stamps) < per_minute:
                stamps.append(now)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(stamps, f)
                os.replace(tmp, path)
                return now - started
            wait = stamps[len(stamps) - per_minute] + WINDOW - now
        if (now - started) + wait > max_wait:
            raise RateLimited("Too Many Requests: %s 共享限速 %d/min，等待 %.0fs 超过上限 %.0fs，本轮跳过"
                              % (name, per_minute, wait, max_wait))
        sleep(max(wait, 0.05))
