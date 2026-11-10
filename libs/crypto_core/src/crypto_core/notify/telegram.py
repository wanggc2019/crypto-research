# -*- coding: utf-8 -*-
"""Telegram Bot API 发送（标准库 urllib，零第三方依赖）。"""
import json
import os
import re
import urllib.error
import urllib.request
import uuid

from .. import timeutil

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def job_label(module, manual_suffix="（手动运行）"):
    """当前调度任务名：launchd 里取 XPC_SERVICE_NAME，手动运行时回退「<module>（手动运行）」。

    所有 Telegram 消息统一用这个标签，保证能区分是哪个任务发的。

    注意：launchd 会把子进程的 XPC_SERVICE_NAME 覆盖成 "0"，所以模板里额外注入
    CRYPTO_JOB_LABEL（值 = Label），这里优先读它。
    """
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    # 只接受本项目的 launchd Label；其它 XPC_SERVICE_NAME（如编辑器/宿主 App 的
    # bundle id）可能包含个人信息，统一回退为「手动运行」。
    if job.startswith("com.crypto-research.") or job.startswith("com.crypto-research"):
        return job
    return "%s%s" % (module, manual_suffix) if manual_suffix else module


def check_credentials(env):
    """返回 (token, chat_id, problems)。"""
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    problems = []
    if not TOKEN_RE.match(token):
        problems.append("TELEGRAM_BOT_TOKEN 格式不对：应为 123456789:AAH... 形式（数字:35+位字符），当前不是")
    if not str(chat_id).lstrip("-").isdigit():
        problems.append("TELEGRAM_CHAT_ID 不是数字：应为一串数字（如 123456789）")
    return token, str(chat_id), problems


def tg_call(token, method, payload, timeout=20):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def send_message(token, chat_id, text, silent=False, disable_preview=False):
    """发送一条 HTML 文本，返回 (ok, error_message)。

    silent=True 时带 disable_notification（不响铃/不弹通知），用于深夜测试消息。
    disable_preview=True 时关闭链接预览卡片（消息里有链接时不再展开 X/网页预览）。
    """
    # 所有消息统一补「发送时间 + 本地时区」，避免只有时间没有时区
    text = str(text or "").rstrip()
    if "🕒 发送时间" not in text:
        footer = "\n\n🕒 发送时间：%s" % timeutil.now_label()
        if len(text) + len(footer) > 4096:
            text = text[: 4096 - len(footer) - 1] + "…"
        text += footer
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(disable_preview),
    }
    if disable_preview:
        payload["link_preview_options"] = {"is_disabled": True}
    if silent:
        payload["disable_notification"] = True
    try:
        res = tg_call(token, "sendMessage", payload)
        return bool(res.get("ok")), ""
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            j = json.loads(raw)
            return False, "%s: %s" % (e.code, j.get("description", raw[:200]))
        except ValueError:
            return False, "%s: %s" % (e.code, raw[:200])
    except Exception as e:  # 网络问题
        return False, "%s: %s" % (type(e).__name__, e)


def discover_chat_id(token):
    """从 getUpdates 取第一条消息的 chat_id（零成本绑定用）。"""
    res = tg_call(token, "getUpdates", {"limit": 20})
    for upd in res.get("result", []) or []:
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    return None


def build_multipart(fields, file_field, filename, file_bytes, boundary=None):
    """构造 multipart/form-data 请求体（纯函数，便于离线单测）。"""
    boundary = boundary or ("----cryptocore" + uuid.uuid4().hex)
    b = boundary.encode()
    chunks = []
    for key, value in fields.items():
        chunks += [
            b"--" + b,
            ('Content-Disposition: form-data; name="%s"' % key).encode(),
            b"",
            str(value).encode("utf-8"),
        ]
    chunks += [
        b"--" + b,
        ('Content-Disposition: form-data; name="%s"; filename="%s"' % (file_field, filename)).encode(),
        b"Content-Type: application/octet-stream",
        b"",
        file_bytes,
        b"--" + b + b"--",
        b"",
    ]
    return b"\r\n".join(chunks), "multipart/form-data; boundary=%s" % boundary


def send_document(token, chat_id, path, caption="", silent=False, timeout=60):
    """上传一个文件（如 Markdown 报告）作为 Telegram 文档；返回 (ok, error)。"""
    try:
        with open(path, "rb") as handle:
            file_bytes = handle.read()
    except OSError as exc:
        return False, "读取文件失败：%s" % exc
    fields = {
        "chat_id": chat_id,
        "caption": str(caption or "")[:1024],
        "parse_mode": "HTML",
    }
    if silent:
        fields["disable_notification"] = "true"
    body, content_type = build_multipart(fields, "document", os.path.basename(path), file_bytes)
    url = "https://api.telegram.org/bot%s/sendDocument" % token
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return bool(res.get("ok")), ""
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw)
            return False, "%s: %s" % (exc.code, payload.get("description", raw[:200]))
        except ValueError:
            return False, "%s: %s" % (exc.code, raw[:200])
    except Exception as exc:  # noqa: BLE001 - 网络错误统一返回
        return False, "%s: %s" % (type(exc).__name__, exc)
