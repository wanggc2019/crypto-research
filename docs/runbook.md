# 定时任务运行与下线 Runbook（launchd）

> 适用：仓库内所有 `crypto-*` 模块的定时任务
> 当前实例（7 个 launchd 任务）：`crypto-news`（每 10 分钟）、`crypto-price`（每 60 秒）、`crypto-x-kol`（每 5 分钟）、`crypto-analyst`（单任务双时间：每日 18:30 日报；周日 22:00 周报+AI 分析）、`crypto-policy`（3 个，待合并）
> 机制定义见 [architecture.md](architecture.md) §3.1 与 §7.6；模块入口见 [crypto-news/README.md](../crypto-news/README.md)、[crypto-price/README.md](../crypto-price/README.md)

**总览（一眼看全部：`make jobs`）**

> `make jobs` 是**只读**看板：读 `~/Library/LaunchAgents/*.plist` + `launchctl print` + 日志 mtime，
> 输出每个任务的 状态 / 模块 / Label / 频率 / 上次运行 / 最近退出码 / runs / 日志，不改任何配置。
> `make jobs json=1` 机器可读；`make jobs strict=1` 存在失败/超时/未加载时退出码 1（可接 CI / 健康检查）。

```
launchd（Label 前缀 com.crypto-research.）
├─ .crypto-price          每 60 秒   → crypto-price/scripts/collect.sh（行情 → 规则 → 推送）
├─ .crypto-x-kol            每 300 秒  → crypto-x-kol/scripts/collect.sh（KOL 动态）
├─ .crypto-news           每 600 秒  → crypto-news/scripts/collect.sh
│                                        ├─ fetch → score → notify → Telegram（P0/P1）
│                                        └─ healthcheck（静默；异常→自愈+告警；未恢复则多级退避调 AI）
└─ .crypto-analyst.weekly  每周日 22:00 → crypto-analyst/scripts/run.sh --notify（数据周报）
└─ .crypto-analyst.daily   每日 18:30   → crypto-analyst/scripts/run.sh --notify（每日简报）
```

**运行 / 检查周期一览**

| 模块 | 运行周期 | 自检 | AI 深度分析 | 每日维护 | 夜间策略 |
|---|---|---|---|---|---|
| crypto-news | **600s**（单轮 20–40s） | **每轮**（正常静默；25 分钟未成功运行判失败；告警 60 分钟去重） | 异常时触发，**首次立即**；未恢复则多级退避 **15→30→60→120→240→360 分钟**；**后台异步**（主任务不等待），单次超时 600s | 当天首轮 `prune` | 全天即时（无静默） |
| crypto-price | **60s**（单轮 2–6s） | **每轮**（正常静默；规则冷却 15–120 分钟；告警 60 分钟去重） | 未启用 | 当天首轮 `prune` | 全天即时（无静默） |

> 更细的阈值（各规则冷却、去重/衰减窗口、保留天数）见各模块
> [crypto-news/technical.md](crypto-news/technical.md#4-运行与检查周期) 与
> [crypto-price/technical.md](crypto-price/technical.md#4-运行与检查周期)。

---

## 1. 它是怎么跑起来的

| 项 | 当前值（crypto-news） |
|---|---|
| 调度器 | macOS **launchd LaunchAgent**（用户登录后由 launchd 托管） |
| Label | `com.crypto-research.crypto-news` |
| plist 模板 | `crypto-news/deploy/launchd/agent.plist.tmpl`（占位符 `__LABEL__` / `__MODULE_DIR__` / `__LOG_DIR__` / `__INTERVAL__`） |
| 安装位置 | `~/Library/LaunchAgents/com.crypto-research.crypto-news.plist` |
| 触发方式 | `StartInterval = 600`（每 600 秒一次）+ `RunAtLoad = true`（登录后先跑一轮） |
| 每轮命令 | `/bin/bash <模块>/scripts/collect.sh` |
| 单轮耗时 | 约 20–40 秒（40 个源），跑完进程退出 |
| 每日维护 | 当天首轮自动执行 `tools/prune.py --apply`（raw 90d / logs 30d / exports 7d / 单日志 2MB 上限） |
| 健康检查 | **不单独定时**：每轮 collect 结束后顺带跑 `tools/healthcheck.py --quiet --notify --fix --agent`，见 §6 |
| AI 深度分析（按需、异步） | 健康检查发现异常后才**后台**拉起 `dsh headless`（不单独定时、无需 DSH 常开、不阻塞主任务），见 §6.1 |

**一轮里发生了什么**

```
launchd 到点
  └─ collect.sh        加锁 data/state/collect.lock（上一轮没跑完就跳过，防重叠）
       └─ run.sh        用仓库根 .venv/bin/python（没有则 python3）依次执行：
            ├─ scripts/fetch.py    40 源采集 → data/raw/<date>/items.jsonl
            ├─ scripts/score.py    过滤/聚类/评分 → data/reports/<date>.md + data/state/alerts-<date>.json
            └─ scripts/notify.py --send  P0/P1 推送 Telegram（24h 去重、夜间只放行 P0）
       └─ 释放锁，日志写入 data/logs/collect.log
```

关键产物与位置：

| 路径 | 内容 |
|---|---|
| `crypto-news/data/logs/collect.log` | 每轮开始/结束与 rc，排查第一入口 |
| `crypto-news/data/logs/launchd.{out,err}.log` | launchd 捕获的 stdout/stderr |
| `crypto-news/data/raw/<date>/items.jsonl` | 当天的原始条目（可回放） |
| `crypto-news/data/state/{alerts-<date>.json,notified.json,seen.json,<source>.json}` | 告警/去重/源状态 |
| `crypto-news/data/reports/<date>.md` | 本地日报 |

**macOS 设置里它长什么样**

「系统设置 → 通用 → 登录项与扩展 → 允许在后台」里显示为 **`bash`**（因为 `ProgramArguments` 是 `/bin/bash`），并带"项目来自身份不明的开发者"提示。这是**自己写的 LaunchAgent 的正常现象**，不是可疑软件；**不要关掉它的开关**，关了等于停掉 crypto-news。

### 1.1 crypto-price（每 60 秒，另一套独立任务）

| 项 | crypto-price |
|---|---|
| Label | `com.crypto-research.crypto-price` |
| plist 模板 | `crypto-price/deploy/launchd/agent.plist.tmpl`（`StartInterval = 60`，字面值，不依赖 `__INTERVAL__`） |
| 每轮命令 | `/bin/bash <repo>/crypto-price/scripts/collect.sh` |
| 单轮耗时 | 约 2–6 秒（3 家交易所 + Binance 永续） |
| 单实例锁 | `crypto-price/data/state/collect.lock`（目录锁 + pid；锁在 `run.py` 内取） |
| 健康检查 | 每轮跑 `tools/healthcheck.py --module crypto-price --quiet --notify --fix`（不带 `--agent`） |
| 关键产物 | `data/state/{last_run,delivery,market_state,alerts-<date>}.json`、`data/reports/`、`data/exports/latest.json` |
| 告警语义 | 全天即时发送（无夜间静默）；P2 只归档；行情源全挂强制告警 |

### 1.2 crypto-x-kol（每 5 分钟，KOL 推文监控）

| 项 | crypto-x-kol |
|---|---|
| Label | `com.crypto-research.crypto-x-kol` |
| plist 模板 | `crypto-x-kol/deploy/launchd/agent.plist.tmpl`（`StartInterval` 由 `crypto-x-kol/config/app.yaml` 的 `poll_seconds=300` 注入） |
| 每轮命令 | `/bin/bash <repo>/crypto-x-kol/scripts/collect.sh` |
| 单轮耗时 | 约 2 分钟（26 个 X 主页；账号间隔 4s，防匿名 20 RPM 限流） |
| 单实例锁 | `crypto-x-kol/data/state/collect.lock` |
| 健康检查 | 每轮 `tools/healthcheck.py --module crypto-x-kol --quiet --notify --fix`（无 `scripts/inspect.sh`，不传 `--agent`） |
| 关键产物 | `data/raw/<date>/posts.jsonl`、`data/reports/<date>.md`、`data/state/kol-<handle>.json`、`data/state/pushed-<date>.json` |
| 推送语义 | 按天去重；全天即时推送（无静默） |
| 数据源 | 匿名 `r.jina.ai`（20 RPM，免 key，不计 token）；不依赖官方 X API |

> 三个模块任务互不影响，各自独立安装/暂停/卸载；共用根 `.env` 的 Telegram 凭据和 `tools/` 工具。

---

## 2. 怎么确认它在跑

```bash
cd ~/vscode/crypto-research

# ① 任务状态：已加载 + last exit code 0
make launchd module=crypto-news action=status

# ② 最近几轮是否按 10 分钟节奏在跑（看 start/done 时间戳）
grep -E "=====" crypto-news/data/logs/collect.log | tail -6

# ③ 最新一轮结果
tail -6 crypto-news/data/logs/collect.log

# ④ 原始数据是否在增长（今天）
wc -l crypto-news/data/raw/$(date +%F)/items.jsonl

# ⑤ 健康检查最近一次结论（JSON）
cat crypto-news/data/state/health.json
```

正常的样子：`状态：已加载 ✅`、`last exit code = 0`、日志里每 10 分钟一对 `start` / `done (rc=0)`。

crypto-price 的对照（每 60 秒）：

```bash
make launchd module=crypto-price action=status
tail -8 crypto-price/data/logs/collect.log
./crypto-price/scripts/run.sh --status          # 最近一轮/冷却/夜间压制/日报
cat crypto-price/data/state/health.json
```

crypto-x-kol 的对照（每 5 分钟）：

```bash
make launchd module=crypto-x-kol action=status
tail -6 crypto-x-kol/data/logs/collect.log        # 应看到「采集完成：26 个源，成功 26」
cat crypto-x-kol/data/state/health.json
cat crypto-x-kol/data/reports/$(date +%F).md     # 当天日报（没新帖时可能不存在）
```

---

## 3. 想立刻跑一轮（不用等 10 分钟）

```bash
# 走 launchd 触发（等价于到点，日志同样落 collect.log）
launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-news

# 或者手工跑一轮（不推送，调试用）
./crypto-news/scripts/run.sh

# crypto-price 同理（它每 60 秒，一般不用手动踢）
launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-price
./crypto-price/scripts/run.sh --dry-run --json
./crypto-price/scripts/run.sh --notify        # 手工跑一轮并允许推送（会推进冷却状态）

# crypto-x-kol（每 5 分钟；单轮约 2 分钟）
launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-x-kol
./crypto-x-kol/scripts/run.sh --dry-run --only willywoo   # 调试单个账号，不写状态
./crypto-x-kol/scripts/run.sh --notify                    # 手工跑一轮并允许推送
```

---

## 4. 暂停 / 卸载 / 彻底下线

三档操作，按目标选，别直接删文件：

| 档位 | 目的 | 操作 | 影响 | 恢复 |
|---|---|---|---|---|
| **A. 临时暂停**（本次登录） | 暂时不跑 | `launchctl bootout gui/$(id -u)/com.crypto-research.crypto-news` | 本会话停止；plist 仍在，**重新登录/重启会再次自动加载** | `make launchd module=crypto-news action=install` |
| **B. 持久暂停**（重启也不跑） | 一段时间不用 | 系统设置里把 `bash` 的后台开关关掉；或 `launchctl disable gui/$(id -u)/com.crypto-research.crypto-news` | 系统记住禁用状态，重启后仍停 | 对应开关打开；或 `launchctl enable …` 后再 install |
| **C. 卸载任务** | 从系统移除定时任务 | `make launchd module=crypto-news action=uninstall`（默认只有这一个） | 停止并删除 `~/Library/LaunchAgents/…plist`；代码和数据保留 | 重新 `action=install` |
| **D. 彻底下线**（不再使用该模块） | 清理干净 | 见下方"彻底下线清单" | 不再运行、不再产生数据 | 从 git 恢复代码 + 重装 |

> crypto-price 把上面命令里的 `module=crypto-news` 换成 `module=crypto-price` 即可；两个任务的 Label 不同，
> 卸载一个不影响另一个。只想临时停价格告警：`launchctl bootout gui/$(id -u)/com.crypto-research.crypto-price`。

### C 的验证（卸载是否成功）

```bash
make launchd module=crypto-news action=uninstall
launchctl print gui/$(id -u)/com.crypto-research.crypto-news   # 应报 "Could not find service"
ls ~/Library/LaunchAgents/com.crypto-research.crypto-news.plist # 应报 No such file
tail -3 crypto-news/data/logs/collect.log                             # 之后不应再出现新的 done
```

> 系统设置的"允许在后台"列表有缓存，卸载后可能不立刻消失；关闭再打开该设置页即可刷新。

### D 彻底下线清单（按顺序）

1. **停任务**：`make launchd module=crypto-news action=uninstall`
2. **停相关的 DSH 自动化任务**（如果有日报/巡检自动化）：`schedule_list` 查出来后逐个删除；没有则跳过。
3. **归档数据**（想留证据就先打包，再删；`data/` 本来就不入库）：

```bash
tar czf ~/crypto-news-data-$(date +%F).tgz -C crypto-news data
rm -rf crypto-news/data        # 可选；删了去重/游标/历史都没了
```

4. **处理凭据**（仓库根 `.env`）：
   - 只是这个模块下线、Telegram 还要用 → 保留；
   - 整个 Bot 不再使用 → 去 Telegram `@BotFather` **撤销 token**，再删除 `.env` 里对应两行。
5. **删代码**（保留 git 历史，随时可恢复）：

```bash
git rm -r crypto-news
git commit -m "chore: 下线 crypto-news"
```

6. **确认没有残留**：

```bash
launchctl print gui/$(id -u)/com.crypto-research.crypto-news 2>&1 | head -2
ls ~/Library/LaunchAgents/ | grep crypto || echo "无残留 plist"
grep -r "crypto-news" .github/ Makefile README.md docs/ 2>/dev/null | grep -v "HANDOFF\|history"
```

---

## 5. 故障排查

| 症状 | 先查什么 | 处理 |
|---|---|---|
| 状态显示"未加载" | plist 是否还在、是否被系统开关禁用 | 重新 `make launchd … action=install`；检查系统设置后台开关 |
| 每轮日志都是 `skip: previous run still in progress` | 上次进程被强杀，锁目录残留 | `rm -rf crypto-news/data/state/collect.lock` |
| `rc=1` | 主源（primary）报价不足（<2 个/资产），或发送失败 | 单个主源降级返回 rc=0（health WARN）；查看 `data/state/last_run.json` 的 `primary_missing` |
| `rc=2` | 凭据格式/缺失 | `.venv/bin/python crypto-news/scripts/notify.py --check` |
| 有采集但没推送 | 去重/没有 P0/P1 | `notify.py --dry-run` 看"待推送"数量是否为 0 |
| 日志文件特别大 | 每天首轮会自动裁剪到 2 MB / 最后 2000 行 | 想立即处理：`make prune module=crypto-news apply=1` |
| 合盖/关机期间没跑 | 正常现象 | 唤醒/登录后 `RunAtLoad` 会补跑一轮；要 7×24 只能上 VPS |
| 报 `Operation not permitted` / 读不到文件 | 项目目录被移动，或落到 `~/Documents`（macOS TCC） | 项目必须留在 `~/vscode/crypto-research`，重新 install |

crypto-price 额外排查：

| 症状 | 先查什么 | 处理 |
|---|---|---|
| 收不到价格告警 | `./crypto-price/scripts/run.sh --test` 验证 Telegram；`--status` 看最近一轮 | 凭据/网络；看 `data/logs/price.log` 与 `collect.log` |
| 告警太频繁 | `data/state/delivery.json` 的 `counts`、`cooldowns`、`held` | 调 `config/rules.yaml` 阈值/冷却/日限额，或调大窗口 |
| 行情源失败 | `--status` 或 `data/raw/<date>/snapshots.jsonl` 的 `errors` | 价格只取 top3；**≥1 个主源能出价就不告警**（消息里显示 2/3、1/3 及异常源）；0 个主源 rc=1 告警；全挂 rc=3 强制推 `data_quality` |
| `rc=4` / 日志 `previous run still in progress` | 刚好上一轮还没结束（单实例锁跳过，正常防重叠；手动跑与 launchd 撞车也会） | 一般无需处理，下一轮自动恢复；连续多轮才检查残留锁（`healthcheck --fix` 会清） |
| 夜晚也收到 P1 | 设计如此（全天即时） | 设计约束不看时间；如需夜间只收 P0，把 `delivery.quiet_enabled` 设为 true |
| 日报没发 | `data/state/report.json` 的 `last_sent_date` | 日报在 18:30 窗口生成；手工 `./scripts/run.sh --report` |

---

## 6. 持续健康检查（healthcheck）

健康检查**已并入主任务**：每轮 `collect.sh` 跑完会顺带执行一次，正常时零输出，异常时推 Telegram + 规则化诊断 + 能自愈的直接修。
另外有一个**仓库级看门狗** `com.crypto-research.watchdog`（每 300 秒），覆盖「主任务完全没跑」这种自检也跑不到的盲区。

| 项 | 值 |
|---|---|
| Label（主任务内嵌） | 各模块主任务（每轮 collect 末尾，无独立 Label） |
| Label（仓库级看门狗，已装） | `com.crypto-research.watchdog`（`StartInterval=300` + `RunAtLoad`） |
| 看门狗行为 | 遍历所有可自检模块（有 `data/logs/collect.log`）→ `healthcheck --module X --json --quiet --fix [--agent]`；再跑 `--module all` 检查全部 launchd 任务；**FAIL 汇总一条** Telegram（批次 digest + 60 分钟冷却）；模块正在跑（`collect.lock` < 30 分钟）则跳过 |
| 周期任务 | **每 24h** 附带一次联网检查（`--network`，可 `--network-every-hours 0` 关闭）；**每天 09:00 后**发一条 WARN/降级汇总（`--warn-digest-hour 0` 表示当天第一次就发） |
| 未接入模块 | 没有 `collect.log` 的（如尚未跑过的新模块）走 launchd 任务检查、不误报；首次跑出 `collect.log` 后自动纳入（`crypto-analyst` 已补 `collect.sh`，下次 18:30 跑完即纳入） |
| 休眠/停机恢复 | 距上一轮看门狗 > **15 分钟**（3×周期）判定为休眠后首轮：该轮**只自愈，不发即时告警、不调 AI**，健康检查的「最近一轮」阈值临时放宽到 24h；下一轮（5 分钟后）恢复正常判定，避免睡眠唤醒后一批假告警 |
| 安装 / 查看 | `.venv/bin/python tools/watchdog.py --install` / `--status` / `--uninstall` |
| 心跳 / 日志 | `data/state/watchdog.json`（每轮结论）、`data/logs/watchdog.log`（每轮一行 + 异常块）、`data/logs/watchdog.{out,err}.log` |
| 单模块 healthcheck 入口 | `<repo>/.venv/bin/python tools/healthcheck.py --module crypto-news --notify --fix --agent`（手动排查用） |

**检查 5 项 + 可选联网**：

| 检查 | 判失败/告警 | 自愈动作（--fix） |
|---|---|---|
| launchd 任务 | 未安装 / 未加载 | 重新 `bootstrap`（受限环境手动跑可能被拒，launchd 里正常） |
| 最近一轮 | 超过 25 分钟没成功跑 / rc≠0 | 无（靠 launchd 自愈+告警） |
| 采集成功率 | secondary 失败忽略；primary 1~2 个记 WARN；0 个才 FAIL 告警 | 无 |
| 残留锁 | 锁超过 30 分钟 | 删除 `data/state/collect.lock` |
| 数据/日志体积 | data >500MB、日志 >2MB | 调 `prune.py --apply` 清理 |
| 凭据 / 网络（`--network`） | 格式不对 / 端点不可达 | 无 |

**Operations**：

```bash
make healthcheck module=crypto-news                    # 手动跑一次（只读）
make healthcheck module=crypto-news fix=1              # 手动自愈
make healthcheck module=crypto-news notify=1           # 有异常就发 Telegram
.venv/bin/python tools/healthcheck.py --module crypto-news --stale-min 0 --notify   # 测试告警链路

# 仓库级看门狗
.venv/bin/python tools/watchdog.py --status            # 是否加载 / 上次退出码 / 周期
.venv/bin/python tools/watchdog.py --once              # 手工扫一轮（会推 Telegram）
.venv/bin/python tools/watchdog.py --once --no-notify --no-agent   # 只看结论，不推送/不调 AI
cat data/state/watchdog.json                           # 每模块最近结论 + launchd 问题
```

> 告警去重：同一个问题 60 分钟内只推一次（状态存 `health.json`），避免每轮刷屏。
> 注意：`rc=4` 现在只记 WARN（单实例锁跳过，属正常防重叠），只有 FAIL 才推 Telegram。
> 噪音：默认只有 **FAIL** 才推 Telegram，WARN 只写 `health.json`；要 WARN 也推，加 `--notify-warn`。
> 每条消息都带 `【模块名】` 和 `🏷 任务：<launchd Label>`，可区分是 news 还是 price 发的。

**异常时自动诊断**：healthcheck 除了告警，还会做规则化原因推断（凭据/网络/权限/依赖/锁/launchd 状态/未按时执行），
结果写进 `health.json` 的 `diagnosis` 字段并附到 Telegram 告警里；能自愈的会先修再报。

### 6.1 AI 深度分析（按需触发 + 后台异步，不单独占定时器）

主任务每 10 分钟跑一轮、健康检查嵌在每轮末尾；另有**仓库级看门狗**每 5 分钟兜底
「主任务完全不跑」的场景——两者都只在异常时才会拉起 AI。
需要"分析原因"时不另设定时器，而是由健康检查在**发现异常时后台拉起** `tools/agent_repair.py`，它再调用 `dsh headless` 起一个新会话做分析和修复；主任务不等待它，跑完自己发修复报告：

| 项 | 说明 |
|---|---|
| 触发条件 | `healthcheck --agent`：只有 `state != ok`（FAIL）才触发；**首次立即调用**，之后按「自上次恢复以来已调用次数」多级退避：**15 / 30 / 60 / 120 / 240 / 360 分钟**（`agent_attempts` 存 `health.json`，恢复正常即清零）。正常日 **0 次调用** |
| 执行 | `tools/agent_repair.py`（后台，独立进程组）→ `crypto-news/scripts/inspect.sh` → `dsh headless`（新会话、最小上下文） |
| 提示词 | [`tools/agents/inspect-prompt.md`](../tools/agents/inspect-prompt.md)（决定它看什么、允许修什么） |
| 允许动作 | 重装/重载任务、清锁、prune、kickstart；**禁止**改代码/config/.env、禁止删 data |
| 产物 | 修复后统一推一条「修复报告」（处理前问题/原因推断/AI 是否执行/处理后是否恢复）；`data/logs/agent-repair.log`（执行器日志）+ `data/logs/inspect.out.log` + `data/state/health-agent.json` |
| 并发 / 超时 | 单实例锁 `data/state/agent.lock`（旧锁 15 分钟自动清）；单次**600 秒**超时，超时也会发「超时/仍异常」报告 |
| 依赖 | 本机 `dsh` CLI + `~/.dsh/.credentials.yaml` 里的 `DEEPSEEK_API_KEY`（`inspect.sh` 自动读取，无需 DSH GUI 常开） |
| 成本 | 每次约 **1–2 万 tokens**（新会话最小上下文）；正常日 0 次；持续故障时按上述退避节奏，最长每 6 小时 1 次 |
| 停用 | 把 `crypto-news/deploy/launchd/healthcheck.plist.tmpl` 里的 `--agent` 去掉并重装（或直接不管，正常日不触发） |
| 可选独立看门狗 | 若想"主任务挂了也能被发现"，可额外装 `… --template healthcheck --install`（每 30 分钟），不装也正常 |

> **多级退避的取舍**：首次立即（尽快恢复）→ 15/30/60 分钟（给重启、瞬时网络故障留恢复窗口）→ 2/4/6 小时（长时间未恢复说明 AI 已无能为力，降低 token 消耗、等人工处理）。恢复正常后 `agent_attempts` 清零，下次新故障仍立即触发。

验证 / 手动执行：

```bash
.venv/bin/python tools/healthcheck.py --module crypto-news --agent --agent-dry-run   # 只看会不会调 agent
.venv/bin/python tools/healthcheck.py --module crypto-news --stale-min 0 --agent      # 模拟异常，真实触发一次 AI 分析
./crypto-news/scripts/inspect.sh                                                      # 直接跑一次巡检
```

> 设计取舍：launchd 健康检查负责「快、确定、7×24」；AI 只在**确实异常时**出场做深度分析。
> 这样既避免"太多后台定时"，也避免正常时白烧 token（原来每 4 小时的会话内自动任务已删除）。

### 6.1.1 消息标签 `🏷 任务：<launchd Label>` 的实现约定

launchd 会给任务进程注入 `XPC_SERVICE_NAME`，但**子进程里该值会被覆盖成 `0`**（2026-11-08 实测），
所以各模块 plist 模板统一注入自定义环境变量 `CRYPTO_JOB_LABEL=__LABEL__`，代码按顺序取：

1. `CRYPTO_JOB_LABEL`（launchd plist 注入 → 真实 Label）
2. `XPC_SERVICE_NAME`（非 0 时）
3. 回退 `<module>（手动运行）`

共享实现：`libs/crypto_core/src/crypto_core/notify/telegram.py::job_label()`。
新增模块的 plist 模板照抄 `EnvironmentVariables` 块即可，不用改代码。

### 6.2 仓库级：所有 launchd 任务健康（"任务挂了"自动报警）

单模块 healthcheck 只管自己；仓库级用 `tools/jobs.py`（**只读**）汇总全部 `com.crypto-research.*` 任务：

| 命令 | 作用 |
|---|---|
| `make jobs` | 总览表格：状态/模块/频率/上次运行/退出码/runs/日志 |
| `make jobs json=1` | 机器可读（JSON） |
| `make jobs strict=1` | 有失败/超时/未加载/降级时退出码 1（人工巡检用） |
| `make jobs errors=1` | 只有**真失败**（rc=2/3、未加载）时退出码 1；降级/超时只打印（`make gate` 用这个，避免生产抖动卡住提交） |
| `make healthcheck-jobs` | 检查全部任务（异常时加 `notify=1` 推 Telegram） |
| `make healthcheck-jobs notify=1` | 异常告警（同一批异常 **60 分钟内只报一次**） |
| `make jobs-alert` | 等价于 `healthcheck-jobs notify=1` 的静默版，给定时任务用 |

**接入点**（已落地）：

- **仓库级看门狗** `com.crypto-research.watchdog`（每 300 秒）：遍历已接入 healthcheck 的模块 + `--module all` 检查全部 launchd 任务，异常汇总成一条 Telegram（60 分钟冷却）；模块正在跑时跳过，避免并发写状态。安装/查看见 §6。
- `make gate` 新增「launchd 任务健康」步骤：有失败/超时/未加载时门禁失败；CI/无 LaunchAgents 环境自动跳过。
- `crypto-news/scripts/collect.sh` 每 10 分钟顺带跑 `tools/healthcheck.py --module all --quiet --notify`，作为二级兜底（看门狗挂了也能报）。

**修复后刷新状态**：`launchctl` 的 `last exit code` 只会在 launchd 真正跑过一次后更新，所以修完要么等下一个周期，要么手动踢一次：

```bash
launchctl kickstart -k gui/$(id -u)/com.crypto-research.<模块>[.<任务>]
```

## 7. 数据保留与日志上限（自动，无需手工）

担心"日志越滚越大"？已经做了自动兜底，**当天首轮会顺手做一次维护**（幂等，用 `data/state/.prune-<date>` 标记）：

| 对象 | 策略 | 稳态体积（crypto-news 实测） |
|---|---|---|
| `data/logs/*.log`（含 `collect.log`、`launchd.*.log`） | 超过 **2 MB** 只留最后 2000 行；超过 **30 天** 删除 | ≤ 2 MB/文件 |
| `data/logs/*` 其余（`fetch-<date>.json` 等） | 保留 **30 天** | 约 3 KB/天 |
| `data/raw/<date>/` | 保留 **90 天** | 约 700 KB/天 → 稳态 ~60 MB |
| `data/exports/*` | 保留 **7 天** | 目前未产出 |
| `data/reports/<date>.md` | 保留 **365 天** | 约 4 KB/天 → ≤ ~1.5 MB |
| `data/state/alerts-<date>.json` | 保留 **180 天**（推送只读当天） | 约 3 KB/天 → ≤ ~0.5 MB |
| `data/state/` 其余 | 不删；`fetch.py` 已对 seen 做规模上限（每源 3000→1000，全局 20000→5000）；`.prune-*` 标记 7 天后清理 | 约 0.2 MB |
| **整个 `data/`** | **硬上限 `--max-data-mb 500`**（见下） | 正常稳态 **约 70–80 MB** |

说明：

- `launchd.out.log` / `launchd.err.log` 正常情况是 **0 字节**——`collect.sh` 内部把输出重定向到 `collect.log`，所以它们不会增长；即使将来有输出，也受上面的 2 MB / 30 天策略约束。
**总数据硬上限（防"打爆 Mac"的兜底）**：`data/` 超过 500 MB 时，即使未到保留期也会从最老的开始删，顺序是
「最老的 raw 日期目录 → 最老的 exports → 最老的 reports → 最老的 alerts → 裁剪日志」，直到回到预算内。
**今天的 raw / alerts / 日志受保护**（正在用，不删）；如果"仅今天的数据"就超预算，会打印 ⚠️ 警告让你查异常源。

- 手工执行：`make prune module=crypto-news`（dry-run，只打印）/ `make prune module=crypto-news apply=1`（真删）。
- 调预算：`python3 tools/prune.py --module crypto-news --max-data-mb 300 --apply`。
- 维护只在 `collect.sh` 跑起来时执行；机器长时间关机就推迟到下次运行，不会丢数据策略。

## 8. 改频率 / 改配置

```bash
# 改轮询间隔（例：300 秒 = 5 分钟），会重写 plist 并重载
.venv/bin/python tools/render_launchd.py --module crypto-news --interval 300 --install
```

- 阈值/权重/信源：改 `crypto-news/config/*.yaml`，**下一轮自动生效**，无需重装（每轮重新读配置）。
- 调度/日志路径/环境变量：改 `crypto-news/deploy/launchd/agent.plist.tmpl` 后重新 `action=install`。

---

## 9. 安全边界

- 凭据只在仓库根 `.env`（gitignored），plist 与日志里没有 token。
- `data/` 全部 gitignored；下线前归档备份即可带走。
- 卸载/下线都不会改动 git 历史，随时可以 `git checkout` 恢复代码。

## 10. headless 会话复用（2026-11-10）

每次 `dsh headless` 默认新建会话，自动任务（日报 AI / 周报 AI / 月季年 AI / 巡检 / 政策解读）反复运行会在工作区里不断堆积会话。现统一改为**固定会话复用**：

- **隔离 home**：自动化 headless 使用独立 `DSH_HOME=~/.dsh-automation`（`headless_session.sh` 自动导出；可用 `DSH_AUTOMATION_HOME` 覆盖），会话写进隔离库，**不出现在 Web 主库工作区**
- 初始化（一次）：`mkdir -p ~/.dsh-automation && cp -RL ~/.dsh/profiles ~/.dsh-automation/profiles && mkdir -p ~/.dsh-automation/logs`
- 公共函数：`tools/headless_session.sh` 的 `headless_run <repo> <sid_file> <prompt>`
- 首次运行：创建会话并把 id 写入 `<module>/data/state/headless-session-<任务>`
- 之后运行：`dsh headless --session-id <id>` 追加到同一会话，**不再新建**
- 会话文件丢失时自动重建并重新锁定
- 覆盖任务：analyst 日报/周报/周期 AI、crypto-news 巡检、crypto-policy 解读

### 10.1 隐藏自动化会话

自动化 headless 会话创建后（或历史上每次运行留下的），用下面命令加入归档集（从分组中隐藏，历史保留）：

```bash
python3 tools/dsh_sessions_archive.py --list    # 预览
python3 tools/dsh_sessions_archive.py --apply   # 备份后写入；重启 DSH 生效
```

Web 里点「显示已归档」可查看；需要恢复时从归档集移除对应 id 即可（`workspace.json` 已自动备份）。
