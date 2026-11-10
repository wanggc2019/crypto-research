# -*- coding: utf-8 -*-
"""告警投递：模板渲染 + Telegram 发送。"""

from . import telegram, templates  # noqa: F401
from .telegram import (  # noqa: F401
    TOKEN_RE,
    build_multipart,
    check_credentials,
    discover_chat_id,
    job_label,
    send_document,
    send_message,
    tg_call,
)
from .templates import MAX_LEN, esc, format_alert  # noqa: F401

__all__ = [
    "templates",
    "telegram",
    "TOKEN_RE",
    "build_multipart",
    "MAX_LEN",
    "esc",
    "format_alert",
    "check_credentials",
    "send_document",
    "send_message",
    "tg_call",
    "job_label",
    "discover_chat_id",
]
