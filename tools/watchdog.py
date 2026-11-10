#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchdog.py —— repo 级常驻看门狗（launchd 每 300 秒调一次 `--once`）。

职责：**薄编排**，不重复实现检查逻辑：
  1. 发现 `crypto-*` 模块（有 scripts/ 或 tests/ 的目录）
  2. 每个模块调用 `tools/healthcheck.py --module X --json --quiet --fix`
     （有 `scripts/inspect.sh` 的模块才加 `--agent`，避免假开关；每 24h 附带一次 `--network`）
  3. 仓库级调用 `tools/healthcheck.py --module all --json --quiet` 检查所有 launchd 任务
  4. 汇总为**一条** Telegram 告警（仅 FAIL + launchd error/warn；批次 digest + 60 分钟冷却）；
     另外每天一条「WARN/降级汇总」，避免降级运行长期静默
  5. 写心跳 `data/state/watchdog.json` 与日志 `data/logs/watchdog.log`
  6. 单实例锁（15 分钟旧锁自清）；模块正在跑（`collect.lock` < 30 分钟）时跳过该模块，避免并发写 health.json

用法：
    python3 tools/watchdog.py --once [--no-notify] [--no-agent] [--dry-run]
    python3 tools/watchdog.py --install | --uninstall | --status
    python3 tools/watchdog.py --once --network-every-hours 0 --warn-digest-hour 0   # 调策略

退出码：0 = 看门狗本身跑完（模块故障通过告警/JSON 表达）；2 = 参数/环境错误。
"""
import argparse
import datetime
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBS = REPO / "libs" / "crypto_core" / "src"
if str(LIBS) not in sys.path:
    sys.path.insert(0, str(LIBS))

LABEL = "com.crypto-research.watchdog"
INTERVAL = 300
MODULE_TIMEOUT = 60
LOCK_STALE_MIN = 15
RUNNING_LOCK_MIN = 30
NOTIFY_COOLDOWN_MIN = 60
NETWORK_EVERY_HOURS = 24      # 每 24h 附带一次联网检查（--network）
WARN_DIGEST_HOUR = 9          # 每天 09:00 后第一次运行时发「降级（WARN）汇总」
SLEEP_GAP_FACTOR = 3          # 距上一轮 > 3×周期（≥15 分钟）→ 视为休眠/停机后首轮
STALE_AFTER_SLEEP_MIN = 1440  # 休眠后首轮把「最近一轮」阈值放宽到 24h，避免误报
STATE_DIR = REPO / "data" / "state"
LOG_DIR = REPO / "data" / "logs"
STATE_PATH = STATE_DIR / "watchdog.json"
LOG_PATH = LOG_DIR / "watchdog.log"
LOCK_DIR = STATE_DIR / "watchdog.lock"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


# ---------------------------------------------------------------- 纯函数（便于单测）
def discover_modules(repo):
    """返回仓库下的 crypto-* 模块名（有 scripts/ 或 tests/）。"""
    out = []
    try:
        children = sorted(Path(repo).iterdir())
    except OSError:
        return out
    for child in children:
        if not child.is_dir() or not child.name.startswith("crypto-"):
            continue
        if (child / "scripts").is_dir() or (child / "tests").is_dir():
            out.append(child.name)
    return out


def lock_age_min(lock, now=None):
    """返回锁目录存在多少分钟；不存在返回 None。"""
    lock = Path(lock)
    if not lock.exists():
        return None
    now = time.time() if now is None else now
    return (now - lock.stat().st_mtime) / 60.0


def should_skip_running(age_min, running_min=RUNNING_LOCK_MIN):
    """模块正在跑（锁不旧）→ 跳过，交给它自己的轮末自检。"""
    return age_min is not None and age_min < running_min


def module_supports_healthcheck(repo, module):
    """模块是否可走单模块 healthcheck（需要 collect.log 才能判「最近一轮」）。

    未部署/新建的 WIP 模块没有 collect.log，交给 launchd 任务总览检查，避免误报；
    模块补了 collect.sh 并跑过一次（生成 collect.log）后自动纳入。
    """
    return (Path(repo) / module / "data" / "logs" / "collect.log").exists()


def should_run_network(last_ts, now_ts=None, every_hours=NETWORK_EVERY_HOURS):
    """距上次联网检查是否已到周期（every_hours<=0 表示关闭）。"""
    try:
        every_hours = int(every_hours)
    except (TypeError, ValueError):
        every_hours = NETWORK_EVERY_HOURS
    if every_hours <= 0:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    return now_ts - float(last_ts or 0) >= every_hours * 3600


def parse_iso_ts(value):
    """watchdog.json 里的 ISO 时间 → timestamp；坏值返回 0。"""
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0


def looks_like_sleep(last_ts, now_ts=None, interval=INTERVAL, factor=SLEEP_GAP_FACTOR):
    """距上一轮看门狗太久（> factor×周期，且 ≥10 分钟）→ 视为休眠/停机后首轮。

    休眠期间 launchd 任务不会跑，唤醒后第一轮如果按正常阈值判「超时/最近一轮未运行」
    会产生一批假告警（还白调 AI），所以这一轮只自愈、不发即时告警、不调 AI。
    """
    if not last_ts:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    limit = max(int(interval) * max(int(factor), 1), 600)
    return now_ts - float(last_ts) > limit


def warn_digest_date(now_ts=None, hour=WARN_DIGEST_HOUR):
    """到点后的「日期」字符串；未到点返回 None（用于每天只发一次降级汇总）。"""
    now = datetime.datetime.fromtimestamp(time.time() if now_ts is None else now_ts)
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = WARN_DIGEST_HOUR
    if hour > 0 and now.hour < hour:
        return None
    return now.strftime("%Y-%m-%d")


def should_send_warn_digest(last_date, now_ts=None, hour=WARN_DIGEST_HOUR):
    day = warn_digest_date(now_ts, hour)
    return bool(day) and last_date != day


def parse_json_output(text):
    """从 healthcheck 输出里取第一个完整 JSON 对象（允许后面跟 AI 打印行）。"""
    text = text or ""
    start = text.find("{")
    if start < 0:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def digest_of(module_states, jobs_problems):
    """批次去重指纹（稳定 hash）：只含 FAIL 模块与 launchd 错误。

    不能把 ok/running 也放进去——「哪个模块正在跑」每轮都会变，会让同一故障
    的指纹变化，绕过 60 分钟冷却（2026-11-09 19:48/19:53 重复告警就是这么来的）。
    """
    parts = ["%s:%s" % (m, s.get("state")) for m, s in sorted(module_states.items())
             if s.get("state") == "fail"]
    parts += ["job:%s:%s" % (p.get("label"), p.get("status"))
              for p in (jobs_problems or []) if isinstance(p, dict)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_message(module_states, jobs_problems, now=None):
    """生成一条聚合告警文本；没有需要告警的项时返回空串。"""
    bad = [(m, s) for m, s in sorted(module_states.items()) if s.get("state") == "fail"]
    jobs = [p for p in (jobs_problems or []) if isinstance(p, dict)]
    if not bad and not jobs:
        return ""
    stamp = datetime.datetime.fromtimestamp(now or time.time()).strftime("%m-%d %H:%M")
    lines = ["🚨【crypto-research】看门狗发现 %d 个问题" % (len(bad) + len(jobs))]
    for module, info in bad:
        lines.append("· %s：有故障（rc=%s）" % (module, info.get("rc")))
        for item in (info.get("fixed") or [])[:3]:
            lines.append("  ├ 已自愈：%s" % str(item)[:80])
        for cause in (info.get("causes") or [])[:2]:
            lines.append("  ├ 原因推断：%s" % str(cause)[:80])
        if info.get("ai"):
            lines.append("  └ AI 深度分析：已触发（报告稍后单独推送）")
        elif info.get("error"):
            lines.append("  └ %s" % str(info["error"])[:100])
        else:
            lines.append("  └ 已尝试自愈仍失败，需人工")
    for problem in jobs[:6]:
        lines.append("· launchd %s：%s（rc=%s，上次运行 %s）" % (
            problem.get("module") or problem.get("label"),
            problem.get("status"), problem.get("last_exit_code", "?"),
            problem.get("last_run_ago", "未知")))
    lines.append("时间：%s" % stamp)
    lines.append("🏷 任务：%s" % LABEL)
    return "\n".join(lines)


def build_warn_digest(module_states, jobs_problems=None, now=None):
    """每日降级（WARN）汇总：模块 WARN + launchd warn 级问题（rc=1/超时）；没有则返回空串。"""
    warns = [(m, s) for m, s in sorted(module_states.items()) if s.get("state") == "warn"]
    jobs = [p for p in (jobs_problems or []) if isinstance(p, dict)]
    if not warns and not jobs:
        return ""
    stamp = datetime.datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
    lines = ["🌤【crypto-research】每日降级汇总 %s（%d 项）" % (stamp, len(warns) + len(jobs))]
    for module, info in warns:
        details = info.get("warn_details") or []
        text = "；".join(str(d)[:70] for d in details[:2]) or "降级运行"
        lines.append("· %s：%s" % (module, text))
    for problem in jobs[:6]:
        lines.append("· launchd %s：%s（rc=%s）" % (
            problem.get("module") or problem.get("label"),
            problem.get("status"), problem.get("last_exit_code", "?")))
    lines.append("说明：仅 FAIL 即时告警；这里是每天一条的 WARN/降级汇总")
    lines.append("🏷 任务：%s" % LABEL)
    return "\n".join(lines)


# ---------------------------------------------------------------- 运行
def log(line):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.datetime.now().strftime("%F %T"), line))


def load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(STATE_PATH))


def acquire_lock():
    if LOCK_DIR.exists():
        age = lock_age_min(LOCK_DIR) or 0
        if age < LOCK_STALE_MIN:
            return None
        shutil.rmtree(str(LOCK_DIR), ignore_errors=True)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    return LOCK_DIR


def run_module(module, notify, agent, network=False, stale_min=None):
    """跑单个模块的 healthcheck，返回 (rc, payload, output, ai_triggered)。"""
    inspect = REPO / module / "scripts" / "inspect.sh"
    cmd = [sys.executable, str(REPO / "tools" / "healthcheck.py"),
           "--module", module, "--json", "--quiet", "--fix"]
    if agent and inspect.is_file():
        cmd.append("--agent")
    if network:
        cmd.append("--network")
    if stale_min:
        cmd += ["--stale-min", str(int(stale_min))]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              timeout=MODULE_TIMEOUT)
        out = (proc.stdout or "") + (proc.stderr or "")
        ai = "AI 深度修复：已异步触发" in out
        return proc.returncode, parse_json_output(out), out, ai
    except subprocess.TimeoutExpired:
        return 2, {"module": module, "state": "fail",
                   "checks": [], "diagnosis": {"causes": []}}, "TIMEOUT", False
    except OSError as exc:
        return 2, {"module": module, "state": "fail",
                   "checks": [], "diagnosis": {"causes": []}}, "OSError: %s" % exc, False


def run_jobs_check():
    """仓库级 launchd 任务检查，返回 (problems, output)。"""
    cmd = [sys.executable, str(REPO / "tools" / "healthcheck.py"),
           "--module", "all", "--json", "--quiet"]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              timeout=MODULE_TIMEOUT)
        out = (proc.stdout or "") + (proc.stderr or "")
        payload = parse_json_output(out)
        problems = payload.get("problems") or []
        return [p for p in problems if isinstance(p, dict)], out
    except (subprocess.TimeoutExpired, OSError) as exc:
        log("jobs check 失败：%s" % type(exc).__name__)
        return [], ""


def notify(text):
    if not text:
        return False
    try:
        from crypto_core import env as envlib
        from crypto_core.notify import check_credentials, send_message
        env = envlib.load_env()
        token, chat, problems = check_credentials(env)
        if problems:
            log("凭据不完整，跳过告警")
            return False
        ok, err = send_message(token, chat, text)
        log("告警发送：%s" % ("ok" if ok else ("失败 %s" % err)))
        return ok
    except Exception as exc:  # noqa: BLE001 - 告警失败不能影响看门狗
        log("告警异常：%s" % exc)
        return False


def run_once(no_notify=False, no_agent=False, dry_run=False,
             network_every_hours=NETWORK_EVERY_HOURS, warn_digest_hour=WARN_DIGEST_HOUR):
    started = time.time()
    modules = discover_modules(REPO)
    if dry_run:
        print("watchdog dry-run：%d 个模块 %s" % (len(modules), ", ".join(modules)))
        print("  notify=%s agent=%s network=%sh warn_digest_hour=%s interval=%ss" % (
            not no_notify, not no_agent, network_every_hours, warn_digest_hour, INTERVAL))
        return 0

    lock = acquire_lock()
    if lock is None:
        log("上一轮仍在运行，跳过")
        return 0
    try:
        old = load_state()
        slept = looks_like_sleep(parse_iso_ts(old.get("ts")))
        if slept:
            log("距上一轮超过 %s 分钟，判定为休眠/停机后首轮：本轮只自愈，不发即时告警、不调 AI" % (
                int(INTERVAL * SLEEP_GAP_FACTOR / 60)))
        agent_this_round = (not no_agent) and not slept
        stale_override = STALE_AFTER_SLEEP_MIN if slept else None
        network_due = should_run_network(old.get("last_network_ts"),
                                         every_hours=network_every_hours)
        module_states, skipped, unmonitored = {}, [], []
        for module in modules:
            if not module_supports_healthcheck(REPO, module):
                unmonitored.append(module)
                continue
            age = lock_age_min(REPO / module / "data" / "state" / "collect.lock")
            if should_skip_running(age):
                skipped.append(module)
                module_states[module] = {"state": "running", "rc": 0, "skipped": True}
                continue
            rc, payload, output, ai = run_module(module, not no_notify, agent_this_round,
                                                 network=network_due, stale_min=stale_override)
            checks = [c for c in (payload.get("checks") or []) if isinstance(c, dict)]
            state = payload.get("state") or "fail"
            fixed = [c.get("detail") for c in checks if c.get("fixed")]
            warn_details = ["%s：%s" % (c.get("name"), c.get("detail"))
                            for c in checks if c.get("state") == "warn"]
            causes = [c.get("cause") for c in ((payload.get("diagnosis") or {}).get("causes") or [])
                      if isinstance(c, dict)]
            module_states[module] = {
                "state": state, "rc": rc, "fixed": fixed, "causes": causes,
                "warn_details": warn_details, "ai": ai,
                "error": "" if rc in (0, 1, 2) else output[-200:],
            }
            if state != "ok":
                log("%s：%s（rc=%s，自愈 %d 项）" % (module, state, rc, len(fixed)))

        jobs_problems, _jobs_out = run_jobs_check()
        # error 级（rc=2/3、未加载等）即时告警；warn 级（rc=1 降级、超时）进每日降级汇总
        job_errors = [p for p in jobs_problems
                      if p.get("level") == "error"
                      and p.get("status") not in ("🟢 正常", "🟢 运行中")]
        job_warns = [p for p in jobs_problems
                     if p.get("level") == "warn"
                     and p.get("status") not in ("🟢 正常", "🟢 运行中")]

        # 1) FAIL 即时告警（批次 digest + 60 分钟冷却）
        text = build_message(module_states, job_errors)
        digest = digest_of(module_states, job_errors)
        same = old.get("last_notify_hash") == digest
        cooling = time.time() - float(old.get("last_notify_ts") or 0) < NOTIFY_COOLDOWN_MIN * 60
        notified = False
        if text and not (same and cooling):
            if no_notify or slept:
                log("（%s）本次告警内容：\n%s" % (
                    "休眠后首轮，降噪跳过发送" if slept else "--no-notify", text))
            else:
                notified = notify(text)
                if notified:
                    old["last_notify_ts"] = time.time()

        # 2) WARN 每日汇总（每天一条，避免降级运行长期静默）
        warn_text = build_warn_digest(module_states, job_warns)
        warn_day = warn_digest_date(hour=warn_digest_hour)
        warn_notified = False
        if warn_text and should_send_warn_digest(old.get("last_warn_digest_date"),
                                                 hour=warn_digest_hour):
            if no_notify or slept:
                log("（%s）本次降级汇总：\n%s" % (
                    "休眠后首轮，降噪跳过发送" if slept else "--no-notify", warn_text))
            else:
                warn_notified = notify(warn_text)
                if warn_notified and warn_day:
                    old["last_warn_digest_date"] = warn_day

        duration = round(time.time() - started, 2)
        states = {}
        for info in module_states.values():
            states[info["state"]] = states.get(info["state"], 0) + 1
        summary = ("watchdog: 检查 %d 模块（运行中跳过 %d，未接入 %d），launchd 失败 %d/降级 %d，"
                   "告警=%s，降级汇总=%s，联网=%s，休眠恢复=%s（%.2fs）" % (
                       len(module_states) - len(skipped), len(skipped), len(unmonitored),
                       len(job_errors), len(job_warns), "yes" if notified else "no",
                       "yes" if warn_notified else "no", "yes" if network_due else "no",
                       "yes" if slept else "no", duration))
        log("run：%s states=%s 未接入=%s" % (
            summary, ",".join("%s×%d" % (k, v) for k, v in sorted(states.items())),
            ",".join(unmonitored) or "-"))

        state = dict(old)
        state.update({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "duration_s": duration,
            "modules": module_states,
            "launchd_errors": job_errors,
            "launchd_warns": job_warns,
            "skipped": skipped,
            "unmonitored": unmonitored,
            "network_checked": bool(network_due),
            "post_sleep": bool(slept),
        })
        if notified:
            state["last_notify_hash"] = digest
        if network_due:
            state["last_network_ts"] = time.time()
        write_state(state)
        print(summary)
        if text:
            print(text)
        if warn_notified and warn_text:
            print(warn_text)
        return 0
    finally:
        shutil.rmtree(str(lock), ignore_errors=True)


# ---------------------------------------------------------------- 安装/卸载/状态
def _plist_dict():
    py = REPO / ".venv" / "bin" / "python"
    python = str(py) if py.exists() else sys.executable
    log_dir = str(LOG_DIR)
    return {
        "Label": LABEL,
        "ProgramArguments": [python, str(REPO / "tools" / "watchdog.py"), "--once"],
        "WorkingDirectory": str(REPO),
        "StartInterval": INTERVAL,
        "RunAtLoad": True,
        "StandardOutPath": os.path.join(log_dir, "watchdog.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "watchdog.err.log"),
        "EnvironmentVariables": {
            "CRYPTO_JOB_LABEL": LABEL,
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "zh_CN.UTF-8",
        },
    }


def _launchctl(*args):
    return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True)


def install():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(_plist_dict()))
    uid = os.getuid()
    _launchctl("bootout", "gui/%d/%s" % (uid, LABEL))
    proc = _launchctl("bootstrap", "gui/%d" % uid, str(PLIST_PATH))
    check = _launchctl("print", "gui/%d/%s" % (uid, LABEL))
    if check.returncode != 0:
        print("❌ 加载失败：%s" % (proc.stderr.strip() or proc.stdout.strip()))
        return 1
    print("已安装并加载：%s" % PLIST_PATH)
    print("周期：%d 秒；日志：%s" % (INTERVAL, LOG_DIR))
    return 0


def uninstall():
    uid = os.getuid()
    _launchctl("bootout", "gui/%d/%s" % (uid, LABEL))
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print("已卸载：%s" % LABEL)
    return 0


def status():
    uid = os.getuid()
    proc = _launchctl("print", "gui/%d/%s" % (uid, LABEL))
    print("plist：%s（%s）" % (PLIST_PATH, "存在" if PLIST_PATH.exists() else "不存在"))
    if proc.returncode != 0:
        print("状态：未加载")
        return 1
    for line in proc.stdout.splitlines():
        if any(k in line for k in ("state =", "last exit code", "run interval", "path =")):
            print(line.strip())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="repo 级看门狗（薄编排 healthcheck）")
    parser.add_argument("--once", action="store_true", help="跑一轮（默认行为）")
    parser.add_argument("--no-notify", action="store_true", help="不推 Telegram（调试/验收用）")
    parser.add_argument("--no-agent", action="store_true", help="不触发 AI 深度分析")
    parser.add_argument("--network-every-hours", type=int, default=NETWORK_EVERY_HOURS,
                        help="每 N 小时附带一次联网检查（默认 24；0=关闭）")
    parser.add_argument("--warn-digest-hour", type=int, default=WARN_DIGEST_HOUR,
                        help="每天几点后发 WARN/降级汇总（默认 9；0=当天第一次就发）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    parser.add_argument("--install", action="store_true", help="安装 launchd 看门狗")
    parser.add_argument("--uninstall", action="store_true", help="卸载 launchd 看门狗")
    parser.add_argument("--status", action="store_true", help="查看看门狗状态")
    args = parser.parse_args(argv)

    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    if args.status:
        return status()
    return run_once(no_notify=args.no_notify, no_agent=args.no_agent, dry_run=args.dry_run,
                    network_every_hours=args.network_every_hours,
                    warn_digest_hour=args.warn_digest_hour)


if __name__ == "__main__":
    sys.exit(main())
