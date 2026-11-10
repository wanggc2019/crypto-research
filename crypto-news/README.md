# crypto-news · 加密情报监测

> 本目录是 `crypto-research` 仓库的一个需求模块（2026-11-08 从仓库根迁入）。
> 仓库级约定见 [../README.md](../README.md)。

个人加密情报监测系统：把分散、嘈杂的加密信息，压缩成每天不超过 10 条、每条能回答"这事跟我仓位有什么关系"的信号。

**关注范围**：BTC / ETH / BNB（SOL、ASTER 暂时冻结；其他山寨不看）
**社交监控**：不监控 KOL，只监控头部交易所创始人/CEO 本人原创（CZ、何一、Brian Armstrong、Ben Zhou 等）
**送达渠道**：Telegram
**阶段**：零成本跑通（无 VPS、无付费 API）

## 文档

- [需求](../docs/crypto-news/requirement.md) · [设计](../docs/crypto-news/design.md) · [业务架构图](../docs/crypto-news/architecture.md) · [技术架构](../docs/crypto-news/technical.md) —— 本模块文档
- [方案 v0.6](../docs/crypto-intel-plan.md) —— 信源分层、五段式流水线、评分模型、黑天鹅早警、落地路线
- [交接文档 HANDOFF](../docs/HANDOFF.md) —— **先读这份**：已确认偏好、当前状态、常用命令、TODO
- [Day-1 零成本清单](../docs/day1-setup.md) —— 建 Telegram Bot、校验配置、跑通采集
- [模块化规划](../docs/code-plan-module-split.md) —— 仓库为什么按需求拆目录

## 配置

| 文件 | 作用 |
|---|---|
| [config/watchlist.yaml](config/watchlist.yaml) | 关注清单（改这里 = 改注意力边界） |
| [config/sources.yaml](config/sources.yaml) | 信源与轮询频率（含实测标记） |
| [config/people.yaml](config/people.yaml) | 高管发声名单与分级路由 |
| [config/entities.yaml](config/entities.yaml) | 实体词典 |
| [config/rules.yaml](config/rules.yaml) | 防噪 / 防骗 / 去重规则 |
| [config/thresholds.yaml](config/thresholds.yaml) | 评分权重与 P0 硬条件 |
| [config/notify.yaml](config/notify.yaml) | Telegram 送达与消息模板 |

凭据不放在本目录：统一读仓库根 `.env`（`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`）；模块内放一份 `.env` 也能覆盖。

## 运行

```bash
cd ~/vscode/crypto-research/crypto-news

# 手动跑一遍：采集 + 评分（不推送、无定时任务）
./scripts/run.sh

# 只跑部分源（调试）；python 用仓库根 .venv，没有则回退系统 python3
../../.venv/bin/python scripts/fetch.py --only binance_ann,sec_press

# 自检：ruff + libs 单测 + 本模块 25 单测 + 端到端评分
./scripts/verify.sh

# 仓库级：一次跑完所有模块（推荐提交前）
cd .. && make verify
```

> 本模块的业务代码在 `scripts/`；通用能力（凭据/配置/存储/事件/去重/推送）在共享库
> [`libs/crypto_core`](../libs/crypto_core/README.md)，由 `scripts/common.py` 负责把共享库
> 加入 `sys.path`（仓库根自动探测，不依赖 PYTHONPATH）。

产物：

- `data/raw/<date>/items.jsonl` —— 归一化原始条目（可回放）
- `data/reports/<date>.md` —— 本地日报（P0/P1/P2 分级）
- `data/state/alerts-<date>.json` —— 供 `notify.py` 推送

**当前状态（2026-11-08）**：Telegram 已打通（`.env` 真实凭据已校验，P0 推送实测成功），`scripts/collect.sh` 由 launchd 每 10 分钟自动执行"采集 → 评分 → 推送"；带锁防重叠；全天即时推送（无夜间静默），同一事件 24h 内只推一次。

```bash
../../.venv/bin/python scripts/notify.py --dry-run   # 只看要发什么（默认）
../../.venv/bin/python scripts/notify.py --discover  # 自动从 getUpdates 补全 CHAT_ID
../../.venv/bin/python scripts/notify.py --test      # 发一条测试消息
../../.venv/bin/python scripts/notify.py --send      # 发送今日 P0/P1（自动去重）
```

### 定时任务（launchd，每 10 分钟）

| 项 | 值 |
|---|---|
| 调度 | macOS launchd LaunchAgent，`StartInterval=600` + `RunAtLoad` |
| Label | `com.crypto-research.crypto-news` |
| 每轮 | `scripts/collect.sh`：加锁 → `fetch.py` → `score.py` → `notify.py --send`，约 20–40 秒后退出 |
| 日志 | `data/logs/collect.log`（每轮 rc）、`data/logs/launchd.{out,err}.log` |
| 系统设置 | 「登录项与扩展 → 允许在后台」里显示为 **`bash`**（自定义 LaunchAgent 的正常现象，**不要关掉开关**） |
| 数据维护 | 每天首轮自动执行 `tools/prune.py`：超 2 MB 日志裁剪、30 天前日志、90 天前 raw、365 天前报告、180 天前 alerts 清理，`data/` 总量 **500 MB 硬上限**；手工：`make prune module=crypto-news` |

```bash
./scripts/install_launchd.sh            # 安装并立即加载（每 600 秒跑一次 collect.sh）
./scripts/install_launchd.sh --status   # 查看状态
./scripts/install_launchd.sh --uninstall

# 立即触发一轮（不用等 10 分钟）
launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-news
```

> **下线/暂停/排查请看完整 Runbook：[docs/runbook.md](../docs/runbook.md)**
> （含临时暂停、持久禁用、卸载、彻底下线与清理清单、故障排查表、改频率）

- ⚠️ 项目必须放在 `~/vscode/crypto-research`：`~/Documents` 受 macOS TCC 保护，launchd 拉起的 bash 读不了，脚本会直接失败。

## 原则

1. 先定义"什么值得打扰我"，再谈抓多少信源。
2. 一手信源优先，二手解读降权；KOL 只当线索。
3. 先跑"只记录不告警"，两周后再开推送。
4. 系统负责"不漏"，人负责"判断"。
