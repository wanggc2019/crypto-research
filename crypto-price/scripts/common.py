#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-price 的薄适配层。

通用能力（路径 / .env / YAML / JSON 原子写 / 文本 / 时间 / 锁）已下沉到共享库
`libs/crypto_core`；这里只负责：
1. 把共享库加入 sys.path（仓库根由文件位置向上探测，不依赖 PYTHONPATH / cwd）；
2. 暴露本模块的固定目录常量与配置加载；
3. 提供本模块会用到的少量编排工具（日志、锁、状态路径）。

约定：本文件不 import 任何业务模块（sources/indicators/rules/delivery），保持薄。
"""
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-price/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import env as _env  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import text as _text  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
LOG_DIR = os.path.join(DATA_DIR, "logs")

CONFIG_FILES = ("watchlist.yaml", "rules.yaml", "app.yaml")
_CFG_CACHE = {}
_DATA_OVERRIDE = None


# ---------------------------------------------------------------- 目录
def ensure_dirs(base=None):
    """创建模块运行目录；base 非空时用于测试/回放（覆盖 data/ 根）。"""
    paths = [CONFIG_DIR, LOG_DIR] if base else [
        CONFIG_DIR, RAW_DIR, STATE_DIR, REPORT_DIR, EXPORT_DIR, LOG_DIR,
    ]
    if base:
        base = str(base)
        paths += [
            base,
            os.path.join(base, "raw"),
            os.path.join(base, "state"),
            os.path.join(base, "reports"),
            os.path.join(base, "exports"),
            os.path.join(base, "logs"),
        ]
    _paths.ensure_dirs(*paths)
    return base or DATA_DIR


def data_paths(base=None):
    """返回数据目录字典；base 非空时把 data/ 整体重定向（测试用）。"""
    root = os.path.abspath(base) if base else DATA_DIR
    return {
        "root": root,
        "raw": os.path.join(root, "raw"),
        "state": os.path.join(root, "state"),
        "reports": os.path.join(root, "reports"),
        "exports": os.path.join(root, "exports"),
        "logs": os.path.join(root, "logs"),
    }


def raw_file(paths, name, date=None):
    """data/raw/<YYYY-MM-DD>/<name>"""
    return os.path.join(paths["raw"], date or _timeutil.today_str(), name)


# ---------------------------------------------------------------- 配置
def load_config(name):
    """读 config/<name>（YAML；PyYAML 缺失时用系统 Ruby 兜底），带进程内缓存。"""
    if name in _CFG_CACHE:
        return _CFG_CACHE[name]
    data = _config.load_yaml(os.path.join(CONFIG_DIR, name))
    if not isinstance(data, dict):
        data = {}
    _CFG_CACHE[name] = data
    return data


def load_all_configs():
    """一次加载三份配置，键为短名：{"watchlist","rules","app"}。"""
    return {
        "watchlist": load_config("watchlist.yaml"),
        "rules": load_config("rules.yaml"),
        "app": load_config("app.yaml"),
    }


def reload_configs():
    _CFG_CACHE.clear()
    return load_all_configs()


def schema_problem(cfg, expected=1):
    return _config.check_schema_version(cfg, expected)


# ---------------------------------------------------------------- 时间/文本/存储
def now():
    return _timeutil.now()


def today_str(dt=None):
    return _timeutil.today_str(dt)


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def epoch(dt=None):
    return int((dt or now()).timestamp())


def sha256(s):
    return _text.sha256(s)


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)


def append_jsonl(path, records):
    return _store.append_jsonl(path, records)


def read_jsonl(path):
    return _store.read_jsonl(path)


def load_env():
    """凭据从仓库根 .env 读（模块内 .env 可覆盖，与 crypto-news 一致）。"""
    return _env.load_env(module_dir=ROOT)


# ---------------------------------------------------------------- 时区
def apply_timezone(name):
    """让进程本地时间跟随配置时区（默认 本地时区）。

    用 TZ 环境变量 + time.tzset()，这样 timeutil.now()/fromtimestamp 与日报日期
    在 UTC VPS 上也按中东时间走，而不是依赖宿主机时区。
    """
    if not name:
        return None
    os.environ["TZ"] = str(name)
    try:
        time.tzset()
    except AttributeError:  # Windows 无 tzset，退化为宿主时区
        pass
    return str(name)


def job_name(module="crypto-price"):
    """调度任务名：优先 CRYPTO_JOB_LABEL（launchd plist 注入；launchd 会把 XPC 覆盖成 0），
    其次 XPC_SERVICE_NAME，最后回退手动标记。"""
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if not job or job == "0":
        return "%s（手动运行）" % module
    return job


def set_data_override(base):
    """让日志等默认产物跟随 --data-dir（测试/回放隔离，避免写模块 data/）。"""
    global _DATA_OVERRIDE
    _DATA_OVERRIDE = os.path.abspath(base) if base else None
    return _DATA_OVERRIDE


# ---------------------------------------------------------------- 日志
def log_line(msg, paths=None, filename="price.log"):
    """追加一行人类可读日志；文件不存在时自动创建。"""
    paths = paths or data_paths(_DATA_OVERRIDE)
    os.makedirs(paths["logs"], exist_ok=True)
    line = "%s %s\n" % (now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(paths["logs"], filename), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    return line.strip()


# ---------------------------------------------------------------- 单实例锁
class Lock:
    """目录锁：mkdir 原子性保证单实例；持有者写 pid，退出时释放。

    过期判定：锁目录 mtime 超过 stale_seconds 视为上一轮被强杀，可抢占。
    """

    def __init__(self, path, stale_seconds=900):
        self.path = str(path)
        self.stale_seconds = int(stale_seconds)
        self.acquired = False

    def acquire(self):
        for _ in range(2):
            try:
                os.makedirs(self.path, exist_ok=False)
                try:
                    with open(os.path.join(self.path, "pid"), "w", encoding="utf-8") as f:
                        f.write(str(os.getpid()))
                except OSError:
                    pass
                self.acquired = True
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    age = 0
                if age > self.stale_seconds:
                    self._remove()
                    continue
                return False
            except OSError:
                return False
        return False

    def release(self):
        if self.acquired:
            self._remove()
            self.acquired = False

    def _remove(self):
        try:
            shutil.rmtree(self.path)
        except OSError:
            # 兜底：逐文件清理后再删目录（残留文件/权限问题）
            try:
                for name in os.listdir(self.path):
                    target = os.path.join(self.path, name)
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        os.unlink(target)
                os.rmdir(self.path)
            except OSError:
                pass


def acquire_lock(paths, stale_seconds=900):
    lock = Lock(os.path.join(paths["state"], "collect.lock"), stale_seconds=stale_seconds)
    return lock if lock.acquire() else None
