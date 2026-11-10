#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""new_module.py —— 生成一个新的需求模块骨架。

用法：
    python3 tools/new_module.py crypto-price --desc "价格与衍生品异动告警"

会同时生成：
  - 代码骨架：<name>/{config,scripts,tests,deploy/launchd,data}
  - 文档骨架：docs/<name>/{README,requirement,design,architecture,technical}.md
生成后按提示手工改 3 处：根 README 模块表、CI job、Makefile 的 MODULES。
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^crypto-[a-z][a-z0-9-]*$")
PLACEHOLDER_WORDS = {"new", "temp", "test", "tmp", "demo", "sample", "xxx", "foo", "bar", "untitled"}
LABEL_PREFIX = "com.crypto-research"

README_TMPL = """# {name} · {desc}

> 本目录是 `crypto-research` 仓库的需求模块；仓库约定见 [../README.md](../README.md) 与
> [../docs/conventions.md](../docs/conventions.md)。共享能力用 `libs/crypto_core`。

## 运行

```bash
./scripts/run.sh            # 跑一轮（不推送）
./scripts/run.sh --dry-run  # 只打印
./scripts/run.sh --notify   # 跑一轮并推送（实现后启用）
python3 scripts/main.py --test   # 验证通道
```

调度（每 10 分钟，模板在 `deploy/launchd/agent.plist.tmpl`）：

```bash
make launchd module={name} action=install
make launchd module={name} action=status
```

## 需求 / 设计 / 业务架构

- [需求文档](../docs/{name}/requirement.md)
- [设计文档](../docs/{name}/design.md)
- [业务架构图](../docs/{name}/architecture.md)
- [技术架构](../docs/{name}/technical.md)

## 约定

- 业务策略（阈值/路由/评分）放本目录 `config/`；通用能力（凭据/HTTP/存储/推送/事件）用 `libs/crypto_core`。
- 运行产物写 `data/`（gitignored）；凭据只读仓库根 `.env`。
- 单测放 `tests/`；`make verify` 必须离线全绿。
"""

CONFIG_TMPL = """schema_version: 1
# {desc}
# 阈值/路由等业务策略放这里；凭据一律走仓库根 .env
enabled: true
"""

COMMON_PY = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— {name} 的薄适配层。

把共享库 libs/crypto_core 加入 sys.path（仓库根由文件位置探测，不依赖 PYTHONPATH/cwd），
并暴露本模块的固定目录。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)  # {name}/
_REPO_ROOT = os.path.dirname(ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as _config  # noqa: E402
from crypto_core import paths as _paths  # noqa: E402
from crypto_core import store as _store  # noqa: E402
from crypto_core import timeutil as _timeutil  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATE_DIR = os.path.join(DATA_DIR, "state")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
LOG_DIR = os.path.join(DATA_DIR, "logs")

_CFG_CACHE = {{}}


def ensure_dirs():
    _paths.ensure_dirs(RAW_DIR, STATE_DIR, REPORT_DIR, LOG_DIR)


def load_config(name):
    if name in _CFG_CACHE:
        return _CFG_CACHE[name]
    data = _config.load_yaml(os.path.join(CONFIG_DIR, name))
    _CFG_CACHE[name] = data
    return data


def now():
    return _timeutil.now()


def today_str():
    return _timeutil.today_str()


def ts_iso(dt=None):
    return _timeutil.ts_iso(dt)


def safe_json_load(path, default):
    return _store.safe_json_load(path, default)


def safe_json_dump(path, obj):
    return _store.safe_json_dump(path, obj)
'''

MAIN_PY = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{name} —— 入口骨架。

实现顺序：读配置 → 取数据（libs/crypto_core）→ 判断策略 → 产出事件/报告 → 推送/落盘。
所有对外副作用都要能被 --dry-run 关掉；--fixtures 用于离线回放（CI 不联网）。

用法：
    python3 scripts/main.py              # 跑一轮，不推送
    python3 scripts/main.py --dry-run    # 只打印
    python3 scripts/main.py --test       # 发测试消息
    python3 scripts/main.py --fixtures <dir>
"""
import argparse
import sys

from common import ROOT, ensure_dirs, load_config, ts_iso  # noqa: E402


def run(args):
    ensure_dirs()
    cfg = load_config("app.example.yaml")
    print("[{name}] {{ts}} config schema_version={{v}} enabled={{e}}".format(
        ts=ts_iso(), v=cfg.get("schema_version"), e=cfg.get("enabled")))
    if args.dry_run:
        print("[dry-run] 不产生外部副作用")
    # TODO: 业务逻辑（用 libs/crypto_core 的 http/market/sources/events/notify）
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="{desc}")
    parser.add_argument("--once", action="store_true", help="跑一轮后退出（默认行为）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    parser.add_argument("--test", action="store_true", help="发一条测试消息")
    parser.add_argument("--fixtures", help="离线回放目录（CI 用，禁止联网）")
    args = parser.parse_args(argv)
    if args.test:
        print("[{name}] test 通道尚未实现")
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
'''

RUN_SH = """#!/usr/bin/env bash
# {name} 一键入口：跑一轮（+ 可选推送）
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(cd .. && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

"$PY" scripts/main.py "$@"
"""

COLLECT_SH = """#!/usr/bin/env bash
# {name} 定时任务入口：加锁跑一轮（日志留痕，防重叠）
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs data/state
LOCKDIR=data/state/collect.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date '+%F %T') skip: previous run still in progress" >> data/logs/collect.log
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
{{
  echo "===== $(date '+%F %T') start ====="
  ./scripts/run.sh
  echo "===== $(date '+%F %T') done (rc=$?) ====="
}} >> data/logs/collect.log 2>&1
"""

SMOKE_TEST = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{name} 冒烟测试（离线）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402


class TestSmoke(unittest.TestCase):
    def test_config_schema(self):
        cfg = common.load_config("app.example.yaml")
        self.assertEqual(cfg.get("schema_version"), 1)

    def test_dirs(self):
        common.ensure_dirs()
        self.assertTrue(os.path.isdir(common.DATA_DIR))


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''

PLIST_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>__LABEL__</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>__MODULE_DIR__/scripts/collect.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>__MODULE_DIR__</string>
  <key>StartInterval</key>
  <integer>__INTERVAL__</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>__LOG_DIR__/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>__LOG_DIR__/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key>
    <string>zh_CN.UTF-8</string>
  </dict>
</dict>
</plist>
"""


def scaffold(name, desc):
    module_dir = REPO / name
    if module_dir.exists():
        raise SystemExit("目录已存在：%s" % module_dir)
    for sub in ("config", "scripts", "tests", "deploy/launchd", "data"):
        (module_dir / sub).mkdir(parents=True, exist_ok=True)

    files = {
        "README.md": README_TMPL.format(name=name, desc=desc),
        "config/app.example.yaml": CONFIG_TMPL.format(desc=desc),
        "scripts/common.py": COMMON_PY.format(name=name),
        "scripts/main.py": MAIN_PY.format(name=name, desc=desc),
        "scripts/run.sh": RUN_SH.format(name=name),
        "scripts/collect.sh": COLLECT_SH.format(name=name),
        "tests/test_smoke.py": SMOKE_TEST.format(name=name),
        "deploy/launchd/agent.plist.tmpl": PLIST_TMPL,
    }
    for rel, content in files.items():
        path = module_dir / rel
        path.write_text(content, encoding="utf-8")
        if rel.endswith(".sh"):
            path.chmod(0o755)

    # 文档骨架：docs/<name>/（从 docs/_template 复制并替换占位符）
    docs_src = REPO / "docs" / "_template"
    docs_dst = REPO / "docs" / name
    if docs_src.is_dir():
        import datetime as _dt

        docs_dst.mkdir(parents=True, exist_ok=True)
        today = _dt.date.today().isoformat()
        for tmpl in sorted(docs_src.glob("*.md")):
            text = tmpl.read_text(encoding="utf-8")
            text = text.replace("{{name}}", name).replace("{{desc}}", desc).replace("{{date}}", today)
            (docs_dst / tmpl.name).write_text(text, encoding="utf-8")
    return module_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成新的需求模块骨架")
    parser.add_argument("name", help="模块目录名，如 crypto-price")
    parser.add_argument("--desc", default="TODO：一句话需求描述", help="一句话描述")
    args = parser.parse_args(argv)

    if not NAME_RE.match(args.name):
        print("❌ 目录名必须是 crypto-<领域词>（小写字母/数字/短横线），收到：%s" % args.name)
        return 2
    topic = args.name[len("crypto-"):]
    if topic in PLACEHOLDER_WORDS:
        print("❌ 不要用占位名（%s）；用领域词，如 crypto-price / crypto-onchain" % topic)
        return 2

    module_dir = scaffold(args.name, args.desc)
    label = "%s.%s" % (LABEL_PREFIX, args.name)
    print("✅ 已生成代码：%s" % module_dir)
    docs_dir = REPO / "docs" / args.name
    if docs_dir.is_dir():
        print("✅ 已生成文档：%s（requirement / design / architecture 待填）" % docs_dir)
    print()
    print("还需要手工做 3 件事：")
    print("  1. 根 README.md 的「模块」表加一行（%s）" % args.name)
    print("  2. .github/workflows/ci.yml 加一个 job（参照 crypto-news）")
    print("  3. Makefile 的 MODULES 加上 %s" % args.name)
    print()
    print("本地验证：")
    print("  ./%s/scripts/run.sh --dry-run" % args.name)
    print("  make test")
    print()
    print("上线调度：")
    print("  make launchd module=%s action=install   # Label: %s" % (args.name, label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
