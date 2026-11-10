#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— crypto-knowledge 的薄适配层。

把共享库 libs/crypto_core 加入 sys.path（仓库根由文件位置探测，不依赖 PYTHONPATH/cwd），
并暴露本模块的固定目录。遵循 docs/conventions.md：模块只向下依赖 libs。
"""
import html
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # crypto-knowledge/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as core_config  # noqa: E402
from crypto_core import env as core_env  # noqa: E402
from crypto_core import store as core_store  # noqa: E402
from crypto_core import timeutil  # noqa: E402
from crypto_core.notify import telegram as core_tg  # noqa: E402

REPO_ROOT = _REPO_ROOT
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_DIR = os.path.join(ROOT, "config")
CURRICULUM_DIR = os.path.join(CONFIG_DIR, "curriculum")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
LOG_DIR = os.path.join(DATA_DIR, "logs")
STATE_PATH = os.path.join(STATE_DIR, "progress.json")
LOCK_DIR = os.path.join(STATE_DIR, "collect.lock")


_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "TELEGRAM_KNOWLEDGE_BOT_TOKEN", "TELEGRAM_KNOWLEDGE_CHAT_ID",
    "TELEGRAM_LEARNING_BOT_TOKEN", "TELEGRAM_LEARNING_CHAT_ID",
)

# 专用 Bot（独立 Bot）优先；兼容几种常见命名，方便按习惯填。
_KNOWLEDGE_TOKEN_KEYS = (
    "TELEGRAM_KNOWLEDGE_BOT_TOKEN",
    "TELEGRAM_LEARNING_BOT_TOKEN",
)
_KNOWLEDGE_CHAT_KEYS = (
    "TELEGRAM_KNOWLEDGE_CHAT_ID",
    "TELEGRAM_LEARNING_CHAT_ID",
)


def load_env():
    """仓库根 .env 为默认值，进程环境变量可覆盖（便于部署/测试）。"""
    env = dict(core_env.load_env(module_dir=ROOT))
    for key in _ENV_KEYS:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _first_value(raw, keys):
    for key in keys:
        if raw.get(key):
            return raw[key], key
    return "", ""


def resolve_bot_env(raw):
    """crypto-knowledge 专用 Bot 优先，否则回退共享 Bot。

    返回 {TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, token_key, chat_key, dedicated}；
    token_key/chat_key 记录实际命中的变量名，供 --whoami 排查。
    """
    token, token_key = _first_value(raw, _KNOWLEDGE_TOKEN_KEYS)
    chat, chat_key = _first_value(raw, _KNOWLEDGE_CHAT_KEYS)
    dedicated = bool(token and chat)
    if not token:
        token, token_key = raw.get("TELEGRAM_BOT_TOKEN", ""), "TELEGRAM_BOT_TOKEN"
    if not chat:
        chat, chat_key = raw.get("TELEGRAM_CHAT_ID", ""), "TELEGRAM_CHAT_ID"
    return {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_CHAT_ID": str(chat or ""),
        "token_key": token_key,
        "chat_key": chat_key,
        "dedicated": dedicated,
    }


def load_bot_env():
    """发给 Telegram 的实际凭据（专用 Bot 优先，未配置回退共享 Bot）。"""
    return resolve_bot_env(load_env())


def job_label():
    return core_tg.job_label("crypto-knowledge")


def load_config(name):
    """读本模块 config/<name>.yaml|json；不存在返回 {}。"""
    return core_config.load_config(ROOT, name)


def ensure_dirs():
    for path in (DATA_DIR, STATE_DIR, REPORT_DIR, LOG_DIR):
        os.makedirs(path, exist_ok=True)


def today_str(dt=None):
    return timeutil.today_str(dt)


def now():
    return timeutil.now()


def ts_iso(dt=None):
    return timeutil.ts_iso(dt)


def load_state(path=None):
    return core_store.safe_json_load(path or STATE_PATH, {})


def save_state(state, path=None):
    core_store.safe_json_dump(path or STATE_PATH, state)


def log_line(text):
    """写模块运行日志（与其它模块一致，便于 healthcheck 解析 collect.log）。"""
    try:
        ensure_dirs()
        with open(os.path.join(LOG_DIR, "crypto-knowledge.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (ts_iso(), text))
    except OSError:
        pass


def esc(value):
    """HTML 转义（与其它模块的展示约定一致，quote=False）。"""
    return html.escape(str(value or ""), quote=False)
