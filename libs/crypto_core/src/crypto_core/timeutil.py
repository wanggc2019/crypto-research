# -*- coding: utf-8 -*-
"""时间与时段工具：ISO 时间、今天、静默时段、时效衰减。

时区由运行环境决定（`TZ` / 系统本地时区）；仓库不硬编码地理位置。
"""
import datetime
import email.utils
import os
import time

TZ_NAME = os.environ.get("CRYPTO_TZ") or (time.strftime("%Z") or "local")
TZ_SHORT = time.strftime("%z") or ""
TZ_LABEL = TZ_NAME
_off = datetime.datetime.now().astimezone().utcoffset() or datetime.timedelta(0)
LOCAL_TZ = datetime.timezone(_off)
DEFAULT_QUIET_SPAN = "22:00-07:00"
DISABLED_QUIET_SPANS = {"", "off", "none", "false", "0"}


def now():
    return datetime.datetime.now()


def today_str(dt=None):
    return (dt or now()).strftime("%Y-%m-%d")


def ts_iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def parse_datetime(value):
    """把 datetime / ISO 字符串 / epoch 秒 / YYYY-MM-DD 解析成 datetime；失败返回 None。"""
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(value), LOCAL_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def to_local(value=None):
    """统一转成 本地时区（）；naive 时间视为本地时间。"""
    dt = parse_datetime(value) if value is not None else now()
    if dt is None:
        dt = now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def fmt_local(value=None, fmt="%Y-%m-%d %H:%M", label=False):
    """本地时间字符串（不输出地理位置/时区名，避免暴露所在地）。"""
    return to_local(value).strftime(fmt)


def now_label(fmt="%Y-%m-%d %H:%M"):
    """当前本地时间，用于消息页脚。"""
    return fmt_local(now(), fmt)


def parse_date(s):
    """把 RSS/网页里的日期字符串解析成带时区的 ISO 字符串；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt:
            return dt.astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(s.replace("Z", "+0000"), fmt)
            return dt.astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def parse_span(span, default=DEFAULT_QUIET_SPAN):
    """把 "22:00-07:00" 解析成 (start_minutes, end_minutes)；off/空返回 None。"""
    text = str(span if span is not None else default).strip()
    if text.lower() in DISABLED_QUIET_SPANS:
        return None
    try:
        start_s, end_s = (span or default).split("-")
        sh, sm = [int(x) for x in start_s.split(":")]
        eh, em = [int(x) for x in end_s.split(":")]
        return sh * 60 + sm, eh * 60 + em
    except (ValueError, AttributeError):
        start_s, end_s = default.split("-")
        sh, sm = [int(x) for x in start_s.split(":")]
        eh, em = [int(x) for x in end_s.split(":")]
        return sh * 60 + sm, eh * 60 + em


def in_quiet_hours(now_dt=None, span=DEFAULT_QUIET_SPAN):
    """静默时段判断（支持跨零点，如 22:00-07:00）；span=off/空 表示全天不发静默。"""
    text = str(span if span is not None else DEFAULT_QUIET_SPAN).strip()
    if text.lower() in DISABLED_QUIET_SPANS:
        return False
    cur = now_dt or now()
    start, end = parse_span(text)
    minute = cur.hour * 60 + cur.minute
    if start > end:  # 跨零点
        return minute >= start or minute < end
    return start <= minute < end


def age_hours(published_at, now_dt=None):
    """发布时间距今小时数；解析失败返回 None。"""
    if not published_at:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    return max(0.0, ((now_dt or now()) - ts).total_seconds() / 3600.0)


def decay(age, half_life_hours):
    """2^(-Δt/h) 时效衰减因子；age 单位小时。"""
    if age is None or half_life_hours is None or half_life_hours <= 0:
        return 1.0
    return 2 ** (-float(age) / float(half_life_hours))
