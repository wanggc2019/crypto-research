# crypto-price · 主流币价格异动实时告警

> 本目录是 `crypto-research` 仓库的一个需求模块（2026-11-08 新建）。
> 仓库级约定见 [../README.md](../README.md) 与 [../docs/conventions.md](../docs/conventions.md)。
> 文档见 [docs/crypto-price/](../docs/crypto-price/README.md)：[需求](../docs/crypto-price/requirement.md) · [设计](../docs/crypto-price/design.md) · [业务架构图](../docs/crypto-price/architecture.md) · [技术架构](../docs/crypto-price/technical.md)。

盯住 **BTC / ETH / BNB** 的现货 + 永续行情，在**价格剧烈波动**时通过 Telegram 告警：

- 急涨急跌（1m/5m/15m/1h 多窗口）
- 24h 大幅波动、24h 新高/新低
- 放量、资金费率极端、持仓量（OI）异动
- 关键整数关口突破/跌破
- 多源价差异常 / 行情源全部不可用

数据源全部**免 API key**：Binance（现货 + USD-M 永续）、OKX（现货）、Coinbase（现货）；
三源取中位数并交叉核验，单源失败自动降级。**不做交易、不接私钥、不买数据。**

## 快速开始

```bash
cd ~/vscode/crypto-research/crypto-price

./scripts/run.sh                     # 跑一轮：写快照/导出，不推送、不写投递审计
./scripts/run.sh --dry-run --json    # 只预览，不写状态
./scripts/run.sh --notify            # 跑一轮并推送（有事件时才发）
./scripts/run.sh --test              # 发一条静默 Telegram 测试消息（验证通道；失败返回 3）
./scripts/run.sh --test-alert        # 用离线冲击快照模拟一条真实格式告警（带【模拟测试】标记，不碰生产状态）
./scripts/run.sh --test-alert --dry-run  # 只看模拟告警卡片，不发送
./scripts/run.sh --status            # 最近运行/冷却/送达状态（不联网）
./scripts/run.sh --report            # 立即生成/推送日报
./scripts/run.sh --report --dry-run  # 只看日报内容

./scripts/verify.sh                  # 本地自检：ruff + libs 单测 + 本模块单测 + fixtures 端到端
./scripts/verify.sh --with-network   # 额外跑一次真实行情预览
```

调度（launchd，每 60 秒一轮）：

```bash
./scripts/install_launchd.sh            # 安装并立即加载
./scripts/install_launchd.sh --status   # 查看状态
./scripts/install_launchd.sh --uninstall

# 或仓库级：
make launchd module=crypto-price action=install
make launchd module=crypto-price action=status
```

## 配置（改这里 = 改注意力边界）

| 文件 | 作用 |
|---|---|
| [config/watchlist.yaml](config/watchlist.yaml) | 监控标的、三家交易所交易对、关键整数关口 |
| [config/rules.yaml](config/rules.yaml) | 8 类规则阈值、P0/P1 分级、冷却、即时送达策略 |
| [config/app.yaml](config/app.yaml) | 数据源端点、轮询周期、衍生品开关、日报时间 |

凭据只读仓库根 `.env`（Telegram `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`），
模块内放 `.env` 可覆盖。本模块所有行情源都**不需要** key；CMC 免费 key 是预留扩展位（默认关闭）。

### 价格与价差怎么算

- 每轮从 Binance / OKX / Coinbase 各取**最新成交价**，三家可用时取**中位数**（2 家取两者平均，1 家直接用）；
- `spread_bps = (最高价 - 最低价) / 中位数 × 10000`，用于衡量三源分歧；
- `spread > 50bps`：本轮价格类规则跳过，发 P1「数据质量异常」；`spread > 100bps`：视为数据不可用；
- 24h 高/低/涨跌幅取**主源**（Binance → OKX → Coinbase 顺序）的统计值，现价用三源中位数；
- 不同交易所价格天然有微小差异：订单簿深度/最新成交 vs 中间价、USDT 与 USD 计价差异、手续费/流动性、网络延迟、套利摩擦等，正常在 0.01%–0.1% 量级；
- 单源失败时会话照常：**只要 top3 里还有 1 个能出价就不告警**；某个源**本轮第一次失败后，同源后续请求直接跳过**，不再等超时；下一轮（60s 后）自动重试；
- **价格只取 top3 大所**：`quality.primary_sources: [binance, okx, coinbase]`，其他源即使 `enabled` 也只做对比校验，不参与定价；
  - **有 1 个主源能出价就不告警**：`quality.min_primary_sources: 1`；0 个主源才视为数据质量不保；
  - 主源 3/3 → 消息显示「多源核验：3/3 binance/okx/coinbase（数据正常）」；
  - 2/3 → 消息显示「2/3 binance/okx/coinbase（coinbase 获取异常）」，不推 Telegram，只写 health WARN/日志；
  - 1/3 → 价格仍可用，但消息会提示「1/3 ...（其他源获取异常）」，不单独告警；
  - 0/3 或全挂 → `rc=3` / `data_quality` P0，强制告警；
  - 想更严格可把 `min_primary_sources` 调成 2 或 3；
  - 多个资产同原因异常时 `data_quality` 合并 1 条；已推送数据质量告警的同一小时内健康检查不重复推。

### 阈值怎么调

- `fast_move.p1_pct`：每个资产、每个窗口的 P1 阈值（%）；P0 = `max(P1×2, 3%)`。
- `day_move.p1_pct` / `p0_pct`：24h 累计波动的两档阈值。
- `volume_spike`：近 5m 量 / 前 55m 均量的倍数；必须叠加价格波动才报。
- `funding` / `open_interest`：杠杆拥挤与仓位变化的极端值。
- `delivery.quiet_hours: "off"`：**不设夜间静默**，P0/P1 都即时发送；`daily_cap_p1: 0` 不设日限额。
- `delivery.cooldown_minutes`：同规则同方向的最小再报间隔；幅度继续扩大 1.5 倍可再报（标"加剧"）。

## 分级与送达

| 级别 | 行为 |
|---|---|
| **P0** | 立即推送（急跌/急涨/极端插针） |
| **P1** | 立即推送；不设夜间静默、不设日限额；仅按规则冷却/去重 |
| **P2** | 只写入 `data/state/alerts-<date>.json` 归档，不推送 |

同一轮多窗口触发只发最强一条；同一事件有冷却；跨重启状态持久化，不会重复刷屏。
行情源全挂时发 `行情源全部不可用，价格监控中断`（P0，强制送达），不会静默失败。

## 产物

```
crypto-price/data/
├── raw/<date>/snapshots.jsonl      # 每轮紧凑快照（可回放/审计，90 天）
├── state/
│   ├── market_state.json           # 24h 极值/上一轮价格/OI 历史
│   ├── delivery.json               # 冷却键、当日计数、送达标记、重试队列
│   ├── alerts-<date>.json          # 每轮判定审计（含未推送）
│   ├── last_run.json               # 最近一轮摘要（rc/源/事件/价格）
│   └── report.json                 # 日报生成/发送游标
├── reports/<date>.md               # 每日报告
├── exports/latest.json             # 跨模块只读契约 price/snapshot/v1
└── logs/{price.log,collect.log}    # 运行日志（prune 自动控制在 2MB）
```

## 架构

```
run.py          编排：加载配置 → 取行情 → 规则 → 送达 → 快照/日报 → 退出码
├── sources.py  多源行情（Binance/OKX/Coinbase）+ 交叉核验 + 降级 + fixtures 回放
├── indicators.py  纯数学：多窗口涨跌、量比、OI 变化
├── rules.py    8 类规则 → 告警事件（P0/P1/P2 + dedupe_key + 幅度）
├── delivery.py 静默/冷却/升级/限额/重试 + alert/v1 审计
├── cards.py    Telegram HTML 价格卡片
├── report.py   每日本地报告 + 早报摘要
└── common.py   路径/配置/日志/单实例锁（共享能力来自 libs/crypto_core）
```

共享能力（HTTP、Telegram、事件模型、去重、时间、存储）在
[`libs/crypto_core`](../libs/crypto_core/README.md)，本模块不复制；行情解析器新增在
`libs/crypto_core/market/{okx,coinbase}.py` 并带内联样本单测。

## 运行保障

- **单实例**：`data/state/collect.lock`（目录锁 + pid）；上一轮未结束则本轮跳过（退出码 4）。
- **健康检查**：每轮 `collect.sh` 里跑 `tools/healthcheck.py --quiet --notify --fix`；
  正常零输出，异常推 Telegram 并尝试自愈（清锁/重载 launchd/清理超限数据）。
- **数据维护**：每天首轮自动 `tools/prune.py --apply`（raw 90d / logs 30d / 单日志 2MB / data 500MB 上限）。
- **退出码**：0 成功 / 1 部分源失败或有发送失败 / 2 配置或凭据错误 / 3 全部行情源不可用 / 4 锁被占用。
- **macOS 休眠**：launchd 唤醒后补跑；`--status` 看最近一轮时间。需要 7×24 时按 Runbook 的 VPS 后路上云。

## 常见问题

| 现象 | 排查 |
|---|---|
| 收不到告警 | `./scripts/run.sh --test` 验证通道；`--status` 看最近一轮 rc；看 `data/logs/price.log` |
| 告警太频繁 | 调高 `rules.yaml` 对应规则阈值 / 降低 `daily_cap_p1` / 加长冷却 |
| 夜间被压制 | 只有 P0 穿透；P1 会在 10:00 日报里汇总（`delivery.quiet_exception` 可调） |
| 行情源失败 | `--status` 或 `data/logs/price.log`；三源互备，单源失败不致命；全挂会强制告警 |
| launchd 没跑 | `./scripts/install_launchd.sh --status`；`tail -f data/logs/collect.log` |

## 消息里怎么区分来源

同一条 Telegram Bot 同时给 `crypto-news` 和 `crypto-price` 发消息，所以每条消息都带来源标识：

- 标题前缀：`【crypto-price】`（价格告警/早报/测试消息）或 `【crypto-news】`（新闻告警）
- 结尾任务标识：`🏷 任务：com.crypto-research.crypto-price`（launchd 注入的进程名；手动运行时显示「手动运行」）
- 每条价格消息都带**多源核验**，例如：
  - `多源核验：3/3 binance/okx/coinbase（数据正常）`
  - `多源核验：2/3 binance/okx/coinbase（coinbase 获取异常）`
  - `多源核验：1/3 binance/okx/coinbase（okx、coinbase 获取异常）`

> 噪音控制：早报默认只写本地文件、不自动推送（`config/app.yaml` 的 `report.send: false`，需要时改 true 或手动 `--report`）；
> 健康检查默认只有 **FAIL** 才推 Telegram，WARN 只写 `health.json`（需要时加 `--notify-warn`）。

> 告警只做提醒，不构成投资建议。完整运行/下线说明见 [Runbook](../docs/runbook.md)。
