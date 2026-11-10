#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcheck.py —— 模块运行期持续健康检查（可自愈 + 可告警）。

检查项：
  1. launchd 任务是否已安装/加载/未被系统禁用（可自愈：重新 bootstrap）
  2. 最近一轮是否按时跑、退出码是否为 0（默认 25 分钟无成功运行即失败）
  3. 最近一轮采集成功率（失败源数量）
  4. 残留锁（上一轮被强杀会留下，可自愈：删除过期锁）
  5. 日志体积 / data 总体积（可自愈：调用 tools/prune.py --apply）
  6. 凭据格式（只读 .env，不联网）
  7. 可选 --network：Binance ping / SEC RSS 连通性

用法：
    python3 tools/healthcheck.py --module crypto-news                 # 只看，不动
    python3 tools/healthcheck.py --module crypto-news --fix           # 自愈
    python3 tools/healthcheck.py --module crypto-news --notify        # 有问题时推 Telegram
    python3 tools/healthcheck.py --module crypto-news --notify --fix  # 定时任务用这个
    python3 tools/healthcheck.py --module crypto-news --agent          # 异常时后台异步调 dsh headless（首次立即，之后多级退避）
    python3 tools/healthcheck.py --module crypto-news --stale-min 0 --notify  # 测试告警
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs" / "crypto_core" / "src"))
from crypto_core import config as core_config  # noqa: E402
from crypto_core import env as envlib  # noqa: E402
from crypto_core.notify import check_credentials, send_message  # noqa: E402

LABEL_PREFIX = "com.crypto-research"
START_RE = re.compile(r"^===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) start =====")
DONE_RE = re.compile(r"^===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) done \(rc=(-?\d+)\) =====")
FETCH_RE = re.compile(r"采集完成：(\d+) 个源，成功 (\d+)，失败 (\d+)")

OK, WARN, FAIL = "ok", "warn", "fail"
RANK = {OK: 0, WARN: 1, FAIL: 2}


def label_for(module):
    return "%s.%s" % (LABEL_PREFIX, module)


def job_name(module):
    """调度任务名：优先 CRYPTO_JOB_LABEL（launchd plist 注入；launchd 会把 XPC 覆盖成 0），
    其次 XPC_SERVICE_NAME，最后回退手动标记。"""
    label = os.environ.get("CRYPTO_JOB_LABEL") or ""
    if label:
        return label
    job = os.environ.get("XPC_SERVICE_NAME") or ""
    if not job or job == "0":
        return label_for(module)
    return job


def module_dir(module):
    return REPO / module


def module_stale_min(module, default=25):
    """按模块 config/app.yaml 的 poll_seconds 推导 stale 阈值（2 个周期，最低 default）。"""
    try:
        data = core_config.load_yaml(module_dir(module) / "config" / "app.yaml") or {}
        seconds = int(data.get("poll_seconds") or 0)
        if seconds > 0:
            return max(default, int(seconds * 2 / 60))
    except (OSError, ValueError, TypeError):
        pass
    return default


def log_path(module):
    return module_dir(module) / "data" / "logs" / "collect.log"


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _launchctl_print(label, attempts=3):
    proc = None
    for i in range(attempts):
        proc = _run(["launchctl", "print", "gui/%d/%s" % (os.getuid(), label)])
        if proc.returncode == 0:
            return proc
        if i + 1 < attempts:
            time.sleep(0.15 * (i + 1))
    return proc


def _size(path):
    if not Path(path).exists():
        return 0
    if Path(path).is_file():
        return Path(path).stat().st_size
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


# ---------------------------------------------------------------- 解析（无副作用，便于测试）
def parse_last_run(text):
    """从 collect.log 文本解析「最近一个完整轮次」：{found,finished,rc,age_min,total,ok,fail,in_progress}"""
    cur = {"started": None, "fetch": None}
    last = None
    for line in text.splitlines():
        m = START_RE.match(line)
        if m:
            cur = {"started": m.group(1), "fetch": None}
            continue
        m = FETCH_RE.search(line)
        if m:
            cur["fetch"] = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue
        m = DONE_RE.match(line)
        if m:
            last = {
                "started": cur["started"],
                "fetch": cur["fetch"],
                "finished": m.group(1),
                "rc": int(m.group(2)),
            }
            cur = {"started": None, "fetch": None}
    out = {"found": last is not None, "in_progress": cur["started"] is not None}
    if last:
        out["started"] = last["started"]
        out["finished"] = last["finished"]
        out["rc"] = last["rc"]
        try:
            dt = datetime.datetime.strptime(last["finished"], "%Y-%m-%d %H:%M:%S")
            out["age_min"] = round((datetime.datetime.now() - dt).total_seconds() / 60.0, 1)
        except ValueError:
            out["age_min"] = None
        if last["fetch"]:
            out["total"], out["ok"], out["fail"] = last["fetch"]
    return out


def check_launchd(module, fix=False):
    label = label_for(module)
    plist = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    if not plist.is_file():
        if fix:
            proc = _run([sys.executable, str(REPO / "tools" / "render_launchd.py"),
                         "--module", module, "--install"])
            if proc.returncode == 0 and plist.is_file():
                return {"name": "launchd", "state": WARN, "detail": "任务未安装，已自动安装", "fixed": True}
            return {"name": "launchd", "state": FAIL,
                    "detail": "任务未安装且自动安装失败：%s" % (proc.stderr.strip() or proc.stdout.strip())[:120]}
        return {"name": "launchd", "state": FAIL, "detail": "任务未安装（%s 不存在）" % plist}
    proc = _launchctl_print(label)
    if proc.returncode == 0:
        return {"name": "launchd", "state": OK, "detail": "已加载"}
    # 未加载：尝试自愈（重新 bootstrap）
    if fix:
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), label)], capture_output=True)
        boot = _run(["launchctl", "bootstrap", "gui/%d" % os.getuid(), str(plist)])
        if _launchctl_print(label).returncode == 0:
            return {"name": "launchd", "state": WARN, "detail": "任务之前未加载，已自动重载", "fixed": True}
        err = boot.stderr.strip()[:120]
        hint = "（若在受限环境手动运行，launchctl 可能被拒绝；launchd 定时任务里会成功）" if "5:" in err else ""
        return {"name": "launchd", "state": FAIL,
                "detail": "任务未加载且重载失败：%s%s" % (err, hint)}
    return {"name": "launchd", "state": FAIL, "detail": "任务未加载（可用 --fix 自动重载）"}


def last_run_state(info, stale_min=25):
    """把 parse_last_run() 的结果翻译成检查项（纯函数，便于单测）。"""
    if not info.get("found"):
        return {"name": "last_run", "state": FAIL, "detail": "日志里找不到完整的一轮（start/done）"}
    if info.get("rc") == 4:
        # rc=4 = 单实例锁被占用，本轮主动跳过；这是防重叠的正常行为，不是故障。
        return {"name": "last_run", "state": WARN,
                "detail": "上一轮仍在运行，本轮按锁跳过（rc=4，正常防重叠）"}
    if info.get("rc") == 1:
        # rc=1 = 部分源失败但有产出；主源仍在，属于降级运行，不需要 Telegram 告警。
        return {"name": "last_run", "state": WARN,
                "detail": "最近一轮 rc=1（部分源失败但有产出，%s）" % info.get("finished")}
    if info.get("rc") != 0:
        return {"name": "last_run", "state": FAIL,
                "detail": "最近一轮 rc=%s（%s）" % (info["rc"], info.get("finished"))}
    if info.get("in_progress"):
        return {"name": "last_run", "state": OK,
                "detail": "当前一轮进行中（上次完成于 %s）" % info.get("finished")}
    if info.get("age_min") is not None and info["age_min"] > stale_min:
        return {"name": "last_run", "state": FAIL,
                "detail": "最近一轮已 %.0f 分钟未运行（阈值 %s 分钟）" % (info["age_min"], stale_min)}
    # 非核心源失败不在日志层面告警：只要核心源（Binance/OKX）有报价，rc 就是 0。
    detail = "最近一轮 %s（%.0f 分钟前，rc=0，%s 源成功）" % (
        info.get("finished"), info.get("age_min") or 0, info.get("ok", 0))
    if info.get("in_progress"):
        detail += "；当前一轮进行中"
    return {"name": "last_run", "state": OK, "detail": detail}


def check_last_run(module, stale_min=25, fix=False):
    path = log_path(module)
    if not path.is_file():
        return {"name": "last_run", "state": FAIL, "detail": "没有 collect.log"}
    info = parse_last_run(path.read_text(encoding="utf-8", errors="ignore"))
    result = last_run_state(info, stale_min)
    # 数据源分级策略（crypto-price 写入 last_run.json）：
    #   primary_ok=false     -> 主源报价不足（<min_primary_sources），数据质量不保 → FAIL
    #   primary_complete=false -> 部分主源降级但数量够 → WARN（不推 Telegram）
    #   secondary 源失败      -> 忽略
    try:
        last = json.loads((module_dir(module) / "data" / "state" / "last_run.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if isinstance(last, dict):
        if last.get("primary_ok") is False:
            missing = last.get("primary_missing") or {}
            detail = "、".join("%s:%s" % (asset, "/".join(names)) for asset, names in missing.items())
            return {"name": "last_run", "state": FAIL,
                    "detail": "主源报价不足（需要至少 2 个主源）：%s" % (detail or "未知")}
        if last.get("primary_ok") is True:
            if last.get("primary_complete") is False:
                return {"name": "last_run", "state": WARN,
                        "detail": "部分主源降级（其余主源可用，价格仍有交叉校验）"}
            if result["state"] != OK:
                return {"name": "last_run", "state": OK,
                        "detail": "主源报价正常（次级源失败或日志告警已忽略）"}
            return result
    return result


def check_lock(module, fix=False):
    lock = module_dir(module) / "data" / "state" / "collect.lock"
    if not lock.exists():
        return {"name": "lock", "state": OK, "detail": "无残留锁"}
    age_min = (time.time() - lock.stat().st_mtime) / 60.0
    if age_min <= 30:
        return {"name": "lock", "state": OK, "detail": "锁存在但在正常运行窗口内（%.0f 分钟）" % age_min}
    if fix:
        subprocess.run(["rm", "-rf", str(lock)], check=False)
        return {"name": "lock", "state": WARN, "detail": "发现过期锁（%.0f 分钟），已删除" % age_min, "fixed": True}
    return {"name": "lock", "state": FAIL,
            "detail": "锁已存在 %.0f 分钟（上一轮可能被强杀/卡死；--fix 可清理）" % age_min}


def check_disk(module, max_data_mb=500, max_log_kb=2048, fix=False):
    data = module_dir(module) / "data"
    total_mb = _size(data) / 1024.0 / 1024.0
    big_logs = [p for p in (data / "logs").glob("*.log") if p.is_file() and p.stat().st_size > max_log_kb * 1024]
    state = OK
    parts = []
    if total_mb > max_data_mb:
        state = FAIL
        parts.append("data 总量 %.0f MB 超预算 %s MB" % (total_mb, max_data_mb))
    elif total_mb > max_data_mb * 0.8:
        state = WARN
        parts.append("data 总量 %.0f MB 接近预算 %s MB" % (total_mb, max_data_mb))
    if big_logs:
        state = WARN if state == OK else state
        parts.append("%d 个日志超 %s KB" % (len(big_logs), max_log_kb))
    fixed = False
    if fix and (state != OK):
        _run([sys.executable, str(REPO / "tools" / "prune.py"), "--module", module, "--apply"])
        fixed = True
        parts.append("已执行 prune 清理")
    if not parts:
        parts.append("data %.0f MB / 日志正常" % total_mb)
    return {"name": "disk", "state": state, "detail": "；".join(parts), "fixed": fixed}


def check_creds(fix=False):
    env = envlib.load_env()
    token, chat, problems = check_credentials(env)
    if problems:
        return {"name": "creds", "state": WARN, "detail": "凭据不完整：%s" % "；".join(problems)}
    return {"name": "creds", "state": OK, "detail": "Telegram 凭据格式正确"}


def check_network(fix=False):
    targets = [
        ("binance", "https://api.binance.com/api/v3/ping"),
        ("sec", "https://www.sec.gov/news/pressreleases.rss"),
    ]
    bad = []
    for name, url in targets:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-research-health/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status >= 400:
                    bad.append("%s HTTP %s" % (name, r.status))
        except Exception as e:  # noqa: BLE001
            bad.append("%s: %s" % (name, type(e).__name__))
    if bad:
        return {"name": "network", "state": WARN, "detail": "；".join(bad)}
    return {"name": "network", "state": OK, "detail": "Binance / SEC 可达"}


# ---------------------------------------------------------------- 诊断（异常时分析原因）
ERROR_HINTS = [
    (r"rc=4|锁被占用|previous run still in progress", "上一轮尚未结束（单实例锁跳过）",
     "正常防重叠；若连续多轮出现，检查是否有残留锁/超长耗时源"),
    (r"rc=2|TOKEN|CHAT_ID|凭据", "凭据缺失或格式错误", "检查仓库根 .env（notify.py --check）"),
    (r"rc=1|失败 [1-9]|部分源失败|URLError|TimeoutError", "部分行情源失败（降级运行）",
     "主源仍在、价格可用；查看 data/logs/price.log 或 raw 快照 errors，持续失败再看网络/限流"),
    (r"rc=3|Timeout|timed out|Connection|URLError|HTTP 4\d\d|HTTP 5\d\d", "网络不可用/被限流", "检查网络或代理，下一轮会自动重试"),
    (r"Operation not permitted|Permission denied|TCC", "路径/系统权限问题", "项目必须在 ~/vscode/crypto-research，检查系统设置里的文件访问权限"),
    (r"ModuleNotFoundError|ImportError|No module named", "依赖或共享库路径问题", "重建根 .venv 或检查 libs/crypto_core/src 是否在路径上"),
    (r"yaml|YAML|配置", "配置文件解析失败", "检查 config/*.yaml 语法（ruby -ryaml 校验）"),
    (r"锁|Lock|lock|already running", "锁/并发问题", "删除残留锁 data/state/collect.lock（--fix 会自动做）"),
    (r"未安装|未加载|Could not find service", "launchd 任务缺失/未加载", "make launchd module=<name> action=install（--fix 会自动做）"),
    (r"未运行|未按时|分钟未运行|超过.*分钟|阈值", "任务未按时执行（休眠/被禁用/卡住）", "检查任务是否被系统禁用、机器是否休眠、是否有残留锁；--fix 会重载任务"),
    (r"OSError|EIO|Input/output error", "磁盘/系统 I/O 异常", "检查磁盘空间与目录权限"),
]
ERROR_LINE = re.compile(r"rc=[1-9]|Traceback|Error|Exception|失败 [1-9]|Operation not permitted|"
                        r"Permission denied|ModuleNotFound|ImportError|Timeout|Connection|No such file", re.I)


def collect_error_lines(module, limit=8):
    """从 collect.log 末尾与 launchd.err.log 收集错误行（最近优先）。"""
    out = []
    logs = module_dir(module) / "data" / "logs"
    for name in ("launchd.err.log", "collect.log"):
        path = logs / name
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        matched = [ln.strip() for ln in lines if ERROR_LINE.search(ln)]
        out.extend(reversed(matched[-limit:]))
    return out[:limit]


def diagnose(module, run_info, checks, errors=None):
    """规则化诊断：返回 {causes:[{signal,cause,hint}], errors:[...]}。errors 传入则跳过读日志（便于测试）。"""
    causes, seen = [], set()
    for name, state, detail in [(c["name"], c["state"], c["detail"]) for c in checks]:
        if state == OK:
            continue
        for pattern, cause, hint in ERROR_HINTS:
            if re.search(pattern, detail, re.I):
                if cause not in seen:
                    seen.add(cause)
                    causes.append({"signal": name, "cause": cause, "hint": hint})
                break
    errors = errors if errors is not None else collect_error_lines(module)
    for err in errors:
        for pattern, cause, hint in ERROR_HINTS:
            if re.search(pattern, err, re.I) and cause not in seen:
                seen.add(cause)
                causes.append({"signal": "log", "cause": cause, "hint": hint})
                break
    if not causes and any(c["state"] == FAIL for c in checks):
        causes.append({"signal": "unknown", "cause": "未知异常，需要看日志",
                       "hint": "查看 data/logs/collect.log 与 launchd.err.log 末尾"})
    return {"causes": causes, "errors": errors[:5]}


# ---------------------------------------------------------------- AI 深度分析（按需，不单独定时）
# 多级退避：首次异常立即触发；之后按「连续未恢复次数」退避，避免故障持续时反复烧 token。
# 单位：分钟。attempts=0 表示自上次 OK 以来还没调用过 AI。
AGENT_COOLDOWN_SCHEDULE = (15, 30, 60, 120, 240, 360)
AGENT_COOLDOWN_MIN = 360  # 冷却上限（兼容旧参数；--agent-min-interval 也按上限处理）


def agent_cooldown_min(attempts, cap=AGENT_COOLDOWN_MIN):
    """已调用 attempts 次之后，距下一次调用至少间隔多少分钟。

    第 1 次之后 15 分钟、第 2 次之后 30、第 3 次 60、第 4 次 120、第 5 次 240、之后 360。
    """
    try:
        n = int(attempts or 0)
    except (TypeError, ValueError):
        n = 0
    idx = 0 if n <= 1 else min(n - 1, len(AGENT_COOLDOWN_SCHEDULE) - 1)
    value = AGENT_COOLDOWN_SCHEDULE[idx]
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = AGENT_COOLDOWN_MIN
    return max(1, min(value, cap)) if cap > 0 else value


def should_run_agent(worst, last_ts=0, now_ts=None, cooldown_min=AGENT_COOLDOWN_MIN, attempts=0):
    """只在有异常、且距上次 AI 分析超过冷却时间时触发（冷却按尝试次数多级退避）。"""
    if worst == OK:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    return now_ts - float(last_ts or 0) >= agent_cooldown_min(attempts, cooldown_min) * 60


def load_state(path):
    """读取 health.json；坏文件/缺字段一律回退空 dict。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def merge_state(old, new):
    """合并状态：保留旧键（last_* 冷却/去重字段），只覆盖本次检查字段。"""
    merged = dict(old) if isinstance(old, dict) else {}
    if isinstance(new, dict):
        merged.update(new)
    return merged


def write_state(path, state):
    """原子写 health.json（失败静默，不影响主流程）。"""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


def notify_digest(checks):
    """告警去重指纹：稳定 hash（跨进程一致，不能用内置 hash()）。"""
    bad = ["%s:%s" % (c.get("name"), c.get("detail")) for c in checks if c.get("state") != OK]
    raw = "|".join(sorted(bad))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def run_agent(module, worst, last_ts=0, dry_run=False, cooldown_min=AGENT_COOLDOWN_MIN,
              timeout=600, attempts=0):
    """异步拉起 tools/agent_repair.py（内部 dsh headless 巡检），不阻塞采集主流程。"""
    if not should_run_agent(worst, last_ts, cooldown_min=cooldown_min, attempts=attempts):
        return {"ran": False, "reason": "ok 或冷却中"}
    script = module_dir(module) / "scripts" / "inspect.sh"
    if not script.is_file():
        return {"ran": False, "reason": "缺少 scripts/inspect.sh"}
    cmd = [sys.executable, str(REPO / "tools" / "agent_repair.py"),
           "--module", module, "--timeout", str(timeout)]
    if dry_run:
        print("  [agent dry-run] %s" % " ".join(cmd))
        return {"ran": False, "reason": "dry-run", "cmd": cmd}
    log_path = module_dir(module) / "data" / "logs" / "agent-repair.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as logf:
        subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=str(REPO))
    print("  AI 深度分析：已异步触发（结束后会发修复报告）")
    return {"ran": True, "async": True}


# ---------------------------------------------------------------- 告警
def _source_summary(module):
    """从 last_run.json 生成「多源核验」摘要（仅 crypto-price 有该字段）。"""
    try:
        last = json.loads((module_dir(module) / "data" / "state" / "last_run.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(last, dict):
        return ""
    health, primary = last.get("source_health"), last.get("primary_sources")
    if not isinstance(health, dict) or not primary:
        return ""
    ok = [name for name in primary if (health.get(name) or {}).get("ok") is True]
    bad = [name for name in primary if name not in ok]
    text = "%d/%d %s" % (len(ok), len(primary), "/".join(primary))
    return text + ("（%s 获取异常）" % "、".join(bad) if bad else "（数据正常）")


def _notify_text(module, checks, fixed, diag=None):
    bad = [c for c in checks if c["state"] != OK]
    lines = ["%s【%s】健康检查：%d 项异常" % (
        "❌" if any(c["state"] == FAIL for c in bad) else "⚠️", module, len(bad))]
    for c in bad:
        lines.append("- [%s] %s：%s" % (c["state"], c["name"], c["detail"]))
    if diag and diag.get("causes"):
        lines.append("原因推断：")
        for item in diag["causes"][:3]:
            lines.append("  · %s —— %s" % (item["cause"], item["hint"]))
    if fixed:
        lines.append("已自动修复：%s" % "、".join(fixed))
    lines.append("时间：%s" % datetime.datetime.now().strftime("%m-%d %H:%M"))
    source = _source_summary(module)
    if source:
        lines.append("多源核验：%s" % source)
    lines.append("🏷 任务：%s" % job_name(module))
    return "\n".join(lines)


def _repair_text(module, before_checks, diag, agent_result, after_checks):
    """AI 处理完之后发的「修复报告」。"""
    bad_before = [c for c in before_checks if c["state"] != OK]
    bad_after = [c for c in after_checks if c["state"] != OK]
    head = "❌ 有故障" if any(c["state"] == FAIL for c in bad_before) else "⚠️ 有告警"
    lines = ["🔧【%s】自动修复报告" % module, "处理前：%s（%d 项）" % (head, len(bad_before))]
    for c in bad_before[:4]:
        lines.append(" · %s：%s" % (c["name"], c["detail"]))
    if diag and diag.get("causes"):
        lines.append("原因推断：%s" % "；".join(x["cause"] for x in diag["causes"][:2]))
    rc = agent_result.get("rc")
    lines.append("AI 巡检：%s" % ("⏱ 超时中止（未完成，已发告警）" if rc == "timeout" else "已执行（rc=%s）" % rc))
    if bad_after:
        lines.append("处理后：❌ 仍异常（需人工，详见日志）")
        for c in bad_after[:3]:
            lines.append(" · %s：%s" % (c["name"], c["detail"]))
    else:
        lines.append("处理后：✅ 已恢复正常")
    lines.append("详情：%s/data/logs/inspect.out.log" % module)
    source = _source_summary(module)
    if source:
        lines.append("多源核验：%s" % source)
    lines.append("时间：%s" % datetime.datetime.now().strftime("%m-%d %H:%M"))
    lines.append("🏷 任务：%s" % job_name(module))
    return "\n".join(lines)


def notify_repair(module, before_checks, diag, agent_result, after_checks):
    """AI 处理完成后统一发一条修复报告（AI 自己不再发 Telegram，避免重复）。"""
    env = envlib.load_env()
    token, chat, problems = check_credentials(env)
    if problems:
        print("  （凭据不完整，跳过修复报告）")
        return False
    ok, err = send_message(token, chat, _repair_text(module, before_checks, diag, agent_result, after_checks))
    print("  修复报告：%s" % ("已发送 ✅" if ok else ("发送失败 ❌ " + (err or ""))))
    return ok


def _recent_data_quality_alert(module, minutes=60):
    """最近 N 分钟内是否已经发过 data_quality 行情告警（避免健康检查重复发）。"""
    try:
        state = json.loads((module_dir(module) / "data" / "state" / "delivery.json")
                           .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    cooldowns = state.get("cooldowns") if isinstance(state, dict) else {}
    if not isinstance(cooldowns, dict):
        return False
    now = time.time()
    for key, item in cooldowns.items():
        if "data_quality" not in str(key):
            continue
        ts = item.get("ts") if isinstance(item, dict) else None
        try:
            if now - float(ts) < minutes * 60:
                return True
        except (TypeError, ValueError):
            continue
    return False


def maybe_notify(module, checks, fixed, diag=None, cooldown_min=60, notify_warn=False):
    bad = [c for c in checks if c["state"] != OK]
    if not bad:
        return False
    # 行情数据质量告警已经在 delivery 层推过（1 条聚合卡片），健康检查不再重复推同一条信息。
    other_bad = [c for c in bad if c["name"] != "last_run"]
    if not other_bad and _recent_data_quality_alert(module):
        print("  （本轮已推送数据质量告警，跳过重复健康检查告警）")
        return False
    worst = max(checks, key=lambda c: RANK[c["state"]])["state"]
    if worst == WARN and not notify_warn:
        print("  （仅有告警/WARN，默认不推 Telegram；需要时加 --notify-warn）")
        return False
    env = envlib.load_env()
    token, chat, problems = check_credentials(env)
    if problems:
        print("  （凭据不完整，跳过 Telegram 告警）")
        return False
    text = _notify_text(module, checks, fixed, diag)
    state_path = module_dir(module) / "data" / "state" / "health.json"
    digest = notify_digest(checks)
    old = load_state(state_path)
    last_ts = old.get("last_notify_ts", 0)
    if old.get("last_notify_hash") == digest and time.time() - float(last_ts or 0) < cooldown_min * 60:
        print("  （同一问题 %s 分钟内已告警过，跳过）" % cooldown_min)
        return False
    ok, err = send_message(token, chat, text)
    print("  健康检查告警：%s" % ("已发送 ✅" if ok else ("发送失败 ❌ " + (err or ""))))
    if ok:
        # 冷却状态必须落盘，否则下一轮又会被 health.json 重写冲掉 → 每轮都发
        write_state(state_path, merge_state(load_state(state_path), {
            "last_notify_ts": time.time(),
            "last_notify_hash": digest,
        }))
    return ok


# ---------------------------------------------------------------- 仓库级 launchd 任务检查（"任务挂了"报警）
JOBS_ALERT_STATE = REPO / "data" / "state" / "jobs-alert.json"
JOBS_ALERT_COOLDOWN_MIN = 60


def _load_jobs_tool():
    """按文件路径加载 tools/jobs.py（避免把 tools 加入包路径）。"""
    import importlib.util
    path = REPO / "tools" / "jobs.py"
    spec = importlib.util.spec_from_file_location("jobs_tool", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jobs_problems(rows):
    """从 jobs.collect() 结果里挑出需要报警的行（error / warn 级别）。"""
    return [r for r in rows if r.get("level") in ("error", "warn")]


def jobs_notify_text(rows):
    """生成 launchd 异常告警文本（HTML 安全）。"""
    import html as _html
    bad = jobs_problems(rows)
    lines = ["⚠️【crypto-research】launchd 任务异常 %d 个" % len(bad), ""]
    for r in bad:
        lines.append("· %s  %s（rc=%s，上次运行 %s）" % (
            _html.escape(str(r.get("module", ""))),
            _html.escape(str(r.get("status", ""))),
            r.get("last_exit_code", "?"),
            _html.escape(str(r.get("last_run_ago", "未知"))),
        ))
    lines += ["", "处理：看日志后修复；刷新退出码用 launchctl kickstart -k gui/<uid>/<label>"]
    lines.append("🏷 任务：launchd-jobs-watchdog")
    return "\n".join(lines)


def check_all_jobs(notify=False, json_out=False, quiet=False, cooldown_min=JOBS_ALERT_COOLDOWN_MIN):
    """仓库级：检查所有 crypto-* launchd 任务，异常时可推 Telegram（带冷却）。"""
    jobs = _load_jobs_tool()
    rows = jobs.collect()
    bad = jobs_problems(rows)
    if json_out:
        print(json.dumps({"problems": bad, "all": rows}, ensure_ascii=False, indent=1))
    if bad:
        print(jobs.render_table(bad))
    elif not quiet:
        print("✅ 全部 launchd 任务正常（%d 个）" % len(rows))
    if not bad or not notify:
        return 1 if bad else 0

    env = envlib.load_env()
    token, chat, problems = check_credentials(env)
    if problems:
        print("  （凭据不完整，跳过 Telegram 告警）")
        return 1
    digest = "|".join(sorted("%s:%s" % (r.get("label"), r.get("status")) for r in bad))
    old = {}
    try:
        old = json.loads(JOBS_ALERT_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if old.get("last_hash") == digest and time.time() - float(old.get("last_ts") or 0) < cooldown_min * 60:
        print("  （同一批异常 %s 分钟内已告警过，跳过）" % cooldown_min)
        return 1
    ok, err = send_message(token, chat, jobs_notify_text(rows))
    if ok:
        JOBS_ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        JOBS_ALERT_STATE.write_text(json.dumps(
            {"last_hash": digest, "last_ts": time.time()}, ensure_ascii=False), encoding="utf-8")
        print("  launchd 异常告警：已发送 ✅")
    else:
        print("  launchd 异常告警发送失败 ❌ %s" % (err or ""))
    return 1


# ---------------------------------------------------------------- 主流程
def main(argv=None):
    parser = argparse.ArgumentParser(description="模块运行期健康检查（可自愈/可告警）")
    parser.add_argument("--module", required=True,
                        help="模块目录名（如 crypto-news）；传 all 检查全部 launchd 任务")
    parser.add_argument("--notify", action="store_true", help="有异常时推 Telegram")
    parser.add_argument("--notify-warn", action="store_true", help="WARN 级别也推 Telegram（默认只推 FAIL）")
    parser.add_argument("--fix", action="store_true", help="尝试自愈（重载任务/清锁/清理超限数据）")
    parser.add_argument("--network", action="store_true", help="附加联网检查（Binance/SEC）")
    parser.add_argument("--stale-min", type=int, default=None,
                        help="多少分钟没有成功运行判为失败（默认按 poll_seconds×2，最低 25）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="正常时不输出（异常才打印，适合嵌入每轮任务）")
    parser.add_argument("--agent", action="store_true",
                        help="异常时调 dsh headless 做深度分析（首次立即，之后多级退避 15/30/60/120/240/360 分钟）")
    parser.add_argument("--agent-min-interval", type=int, default=AGENT_COOLDOWN_MIN,
                        help="AI 冷却上限（分钟，默认 360；实际取多级退避与上限的较小值）")
    parser.add_argument("--agent-timeout", type=int, default=600, help="单次 AI 分析超时秒数（异步，默认 600）")
    parser.add_argument("--agent-dry-run", action="store_true", help="只打印 agent 命令，不真正运行")
    args = parser.parse_args(argv)

    if args.module in ("all", "jobs"):
        return check_all_jobs(notify=args.notify, json_out=args.json, quiet=args.quiet)

    if not module_dir(args.module).is_dir():
        print("❌ 模块不存在：%s" % (REPO / args.module))
        return 2

    stale_min = args.stale_min if args.stale_min is not None else module_stale_min(args.module)
    checks = [
        check_launchd(args.module, args.fix),
        check_last_run(args.module, stale_min, args.fix),
        check_lock(args.module, args.fix),
        check_disk(args.module, fix=args.fix),
        check_creds(),
    ]
    if args.network:
        checks.append(check_network())
    fixed = [c["detail"] for c in checks if c.get("fixed")]
    worst = max(checks, key=lambda c: RANK[c["state"]])["state"]
    diag = diagnose(args.module, None, checks) if worst != OK else {"causes": [], "errors": []}

    quiet = args.quiet and worst == OK and not args.json
    if quiet:
        pass
    elif args.json:
        print(json.dumps({"module": args.module, "state": worst, "checks": checks,
                          "diagnosis": diag, "fixed": fixed,
                          "ts": datetime.datetime.now().isoformat(timespec="seconds")},
                         ensure_ascii=False, indent=1))
    else:
        icons = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
        print("== %s 健康检查 ==" % args.module)
        for c in checks:
            print(" %s %-10s %s" % (icons[c["state"]], c["name"], c["detail"]))
        if fixed:
            print(" 🔧 已自愈：%s" % "；".join(fixed))
        if diag["causes"]:
            print(" 🔎 原因推断：")
            for item in diag["causes"][:3]:
                print("    - %s → %s" % (item["cause"], item["hint"]))
            if diag["errors"]:
                print("    最近错误行：")
                for err in diag["errors"][:3]:
                    print("      %s" % err[:120])
        print(" 结论：%s" % {"ok": "正常", "warn": "有告警", "fail": "有故障"}[worst])

    state_path = module_dir(args.module) / "data" / "state" / "health.json"
    state = merge_state(load_state(state_path), {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "state": worst,
        "checks": checks,
        "diagnosis": diag,
        "fixed": fixed,
    })
    if worst == OK:
        # 恢复正常 → 重置 AI 多级退避计数，下次故障可立即再调
        state["agent_attempts"] = 0
    write_state(state_path, state)

    if args.notify:
        maybe_notify(args.module, checks, fixed, diag, notify_warn=args.notify_warn)

    if args.agent and worst == FAIL:
        state = load_state(state_path)
        last = state.get("last_agent_ts", 0)
        attempts = state.get("agent_attempts") or 0
        result = run_agent(args.module, worst, last, dry_run=args.agent_dry_run,
                           cooldown_min=args.agent_min_interval, timeout=args.agent_timeout,
                           attempts=attempts)
        if result.get("ran"):
            write_state(state_path, merge_state(load_state(state_path), {
                "last_agent_ts": time.time(),
                "last_agent_spawned_ts": time.time(),
                "last_agent_state_before": worst,
                "agent_attempts": int(attempts) + 1,
            }))
    return {"ok": 0, "warn": 1, "fail": 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
