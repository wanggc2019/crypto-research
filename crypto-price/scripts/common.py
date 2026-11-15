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
from crypto_core.notify import telegram as _tg  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
LOG_DIR = os.path.join(DATA_DIR, "logs")

CONFIG_FILES = ("watchlist.yaml", "rules.yaml", "app.yaml")
_CONFIG = _config.ConfigCache(ROOT)
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
    """返回数据目录字典；base 非空时把 data/ 整体重定向（测试/回放用）。"""
    return _paths.data_layout(base or DATA_DIR)


def raw_file(paths, name, date=None):
    """data/raw/<YYYY-MM-DD>/<name>"""
    return os.path.join(paths["raw"], date or _timeutil.today_str(), name)


# ---------------------------------------------------------------- 配置
_CONFIG = _config.ConfigCache(ROOT)


def load_config(name):
    """读 config/<name>（.yaml/.yml/.json 自动识别；PyYAML 缺失时共享库回退 ruby）；进程内缓存，非 dict 视为 {}。"""
    return _CONFIG.get(name)


def load_all_configs():
    """一次加载三份配置，键为短名：{"watchlist","rules","app"}。"""
    return {
        "watchlist": load_config("watchlist.yaml"),
        "rules": load_config("rules.yaml"),
        "app": load_config("app.yaml"),
    }


def reload_configs():
    _CONFIG.clear()
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


_PRICE_TOKEN_KEYS = ("TELEGRAM_PRICE_BOT_TOKEN", "TELEGRAM_PRICE_BOT")
_PRICE_CHAT_KEYS = ("TELEGRAM_PRICE_CHAT_ID",)


def load_env():
    """凭据从仓库根 .env 读（模块内 .env 可覆盖）。

    价格模块专用 Bot：配置 TELEGRAM_PRICE_BOT_TOKEN / TELEGRAM_PRICE_CHAT_ID 后，
    本模块所有消息（告警/日报/测试/健康检查）都走这个 Bot；未配置则回退共享 Bot。
    """
    env = _env.load_env(module_dir=ROOT)
    info = _env.resolve_bot_env(env, _PRICE_TOKEN_KEYS, _PRICE_CHAT_KEYS)
    if info["token_key"] in _PRICE_TOKEN_KEYS:
        env["TELEGRAM_BOT_TOKEN"] = info["TELEGRAM_BOT_TOKEN"]
    if info["chat_key"] in _PRICE_CHAT_KEYS:
        env["TELEGRAM_CHAT_ID"] = info["TELEGRAM_CHAT_ID"]
    return env


# ---------------------------------------------------------------- 时区
def apply_timezone(name):
    """让进程本地时间跟随配置时区（TZ + tzset；见 crypto_core.timeutil.apply_timezone）。"""
    return _timeutil.apply_timezone(name)


def job_name(module="crypto-price"):
    """调度任务名（launchd Label / 手动运行标记），统一由 crypto_core.notify.telegram.job_label 解析。"""
    return _tg.job_label(module)


def set_data_override(base):
    """让日志等默认产物跟随 --data-dir（测试/回放隔离，避免写模块 data/）。"""
    global _DATA_OVERRIDE
    _DATA_OVERRIDE = os.path.abspath(base) if base else None
    return _DATA_OVERRIDE


# ---------------------------------------------------------------- 日志
def log_line(msg, paths=None, filename="price.log"):
    """追加一行人类可读日志；文件不存在时自动创建。"""
    paths = paths or data_paths(_DATA_OVERRIDE)
    return _store.append_log_line(os.path.join(paths["logs"], filename), msg,
                                  ts=now().strftime("%Y-%m-%d %H:%M:%S"))


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
