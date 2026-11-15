# -*- coding: utf-8 -*-
"""凭据/环境变量：唯一来源是仓库根 `.env`。

模块内如果放了 `.env`，优先用模块内的（便于单模块临时覆盖），
否则回退到仓库根 `.env`；两者都没有时返回空 dict，不报错。
"""
import os
from pathlib import Path

from . import paths


def find_env_file(module_dir=None):
    """按「模块内 .env → 仓库根 .env」顺序找凭据文件。"""
    if module_dir:
        candidate = Path(module_dir) / ".env"
        if candidate.is_file():
            return candidate
    return paths.repo_root() / ".env"


def load_env(path=None, module_dir=None):
    """读 .env 为 dict；不存在返回 {}。"""
    p = Path(path) if path else find_env_file(module_dir)
    out = {}
    if not p.is_file():
        return out
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save_env_value(key, value, path=None, module_dir=None):
    """原地更新 .env 中某个 key（不存在则追加），保留其他行与注释。"""
    p = Path(path) if path else find_env_file(module_dir)
    lines = []
    if p.is_file():
        lines = p.read_text(encoding="utf-8").splitlines()
    hit = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = "%s=%s" % (key, value)
            hit = True
    if not hit:
        lines.append("%s=%s" % (key, value))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def get(key, default=None):
    """读当前进程环境（供部署时覆盖 .env，如 DATA_DIR / CHANNEL_TYPE）。"""
    return os.environ.get(key, default)


def first_value(raw, keys):
    """按顺序返回第一个非空的 (value, key)；都为空返回 ("", "")。"""
    for key in keys:
        if raw.get(key):
            return raw[key], key
    return "", ""


def resolve_bot_env(raw, token_keys=(), chat_keys=()):
    """模块专用 Telegram Bot 凭据优先，未配置则回退共享 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID。

    token 与 chat 各自独立回退（只配了专用 token 时 chat 仍可用共享的）。
    返回 {TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, token_key, chat_key, dedicated}：
    token_key/chat_key 是实际命中的变量名（供 --whoami 排查）；dedicated = 专用 token 与 chat 都已配置。
    """
    token, token_key = first_value(raw, token_keys)
    chat, chat_key = first_value(raw, chat_keys)
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
