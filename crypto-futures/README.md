# crypto-futures · 合约持仓结构（多空比）监控与告警

> 本目录是 `crypto-research` 仓库的需求模块；仓库约定见 [../README.md](../README.md) 与
> [../docs/conventions.md](../docs/conventions.md)。共享能力用 `libs/crypto_core`。

盯住 BTC / ETH / BNB 永续合约的**持仓结构**：做多的账户占比、大户多空持仓比、合约主动买卖量、OI、资金费率。
出现「账户比与大户持仓比背离」「账户比急转」「主动买卖失衡」「拥挤加杠杆」时推 Telegram，
卡片写清楚口径（Binance USD-M 单所）、**说明了什么、不说明什么**和历史参照。全部用 Binance 公开接口，零 key、零成本。

## 运行

```bash
./scripts/run.sh                         # 跑一轮（不推送，只写 data/）
./scripts/run.sh --notify                # 跑一轮并推送（launchd 用）
./scripts/run.sh --dry-run --json        # 联网取数但不写盘、不推送（冒烟用）
python3 scripts/run.py --test            # 发一条测试消息（标明「测试」）
python3 scripts/backtest.py --data <dir> # 用历史 5m 数据回放规则，看各规则推送量（改阈值后必跑）
python3 scripts/backtest.py --cache data/state [--data <资金费率结算记录目录>]  # 直接用线上 30 天缓存回放
python3 scripts/e2e.py                   # 离线端到端回放（make gate 自动跑）
```

推送用 crypto-futures 专用 Bot：根 `.env` 的 `TELEGRAM_FUTURES_BOT_TOKEN` / `TELEGRAM_FUTURES_CHAT_ID`，
token 和 chat 各自没填就回退共享 Bot 的（`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`）。只填 token 时，
新 Bot 要能往共享 chat 发消息：私聊先对它发一次 /start，群聊要把它拉进群，否则发送会被 Telegram 拒绝（403）。

健康告警：平时由仓库级 watchdog 统一发，走**共享 Bot**；watchdog 失效时本模块每轮兜底自查（`healthcheck.py`），那时走**专用 Bot**。

调度：每 300 秒（`config/app.yaml` 的 `poll_seconds`），模板 `deploy/launchd/agent.plist.tmpl`：

```bash
make launchd module=crypto-futures action=install
make launchd module=crypto-futures action=status
```

首轮会从接口回补 7 天历史（约 60 次请求），并在当天第一次拉 30 天缓存时用它回放预热「历史参照」（约 216 次请求 + 1 分钟计算）。

## 规则（阈值在 `config/rules.yaml`）

| 规则 | 级别 | 条件（5m 点，「N 小时前」按时间戳精确对齐） |
|---|---|---|
| 拥挤加杠杆 `crowding_build` | P0 | OI 4h ≥ +5%，且账户比 ≥ 7 天 p95 或 ≤ p5 |
| 账户比与大户持仓比背离 `smart_divergence` | P1 | 24h 账户比 ≥ +15% 且大户持仓比 ≤ −15%（或反过来）；冷却 12h |
| 账户比急转 `retail_shift` | P1 | 1h 账户比变化绝对值 ≥ 20% |
| 合约主动买卖失衡 `taker_burst` | P1 | 15 分钟 Σ买 / Σ卖 ≥ 3.5 或 ≤ 1/3.5，且成交量 ≥ 前 24h 中位数 3 倍 |
| 账户比创 7 天极值 `extreme_level` | P2 | 只落盘，不推送 |
| 资金费率极端 `funding_extreme`（M2） | P1 / P0 | 当前资金费率（`premiumIndex` 实时值）绝对值 ≥ 0.10% 为 P1、≥ 0.20% 为 P0；冷却 2h |
| 持仓量急变 `oi_spike`（M2） | P1 / P0 | 实时 OI 15 分钟变化 ≥ 5%（P0 ≥ 10%），且合约标记价同向变动 ≥ 0.5%；参考点只取 −15 / −20 分钟桶；冷却 1h |

M2 两条规则于 2026-11-17 从 crypto-price 迁入（[需求](../docs/crypto-futures/requirement-m2.md)），基于每轮的实时快照（`RT` 序列），
阈值沿用 crypto-price 原值；回退：把 `config/rules.yaml` 里对应规则 `enabled` 改为 `false`。

治理：同规则 + 同资产 + 同方向冷却 4h（另有规定的除外）；冷却期内幅度 ≥1.5 倍且距上次 ≥1h 可「加剧」再报；同一资产同一数据点只出一张卡；P0 每资产每本地日 ≤2 条。
29.5 天回测约 0.53 条/天；加上 M2 两条规则后为 0.56 条/天（`oi_spike` 1 次，`funding_extreme` 按结算值 0 次）（详见 [需求 §9](../docs/crypto-futures/requirement.md)）。

## 产物

| 路径 | 内容 |
|---|---|
| `data/exports/latest.json` | 跨模块契约 `futures/snapshot/v1`（每资产三项比值、OI、合约价、资金费率、当日触发数） |
| `data/state/history-<资产>.json` | 本地 5m 历史（7 天） |
| `data/state/cache30d-<资产>.json` | 30 天缓存（每天覆盖写，只给历史参照用） |
| `data/state/triggers.json` | 触发记录（含之后 24h 涨跌，保留 365 天） |
| `data/state/last_run.json` | healthcheck 读取：任一接口超过 1 小时无新数据时标记失效并说明停用了哪些规则 |
| `data/raw/<日期>/cards.jsonl`、`p2.jsonl` | 每轮推送卡片与 P2 记录 |

## 文档

- [需求](../docs/crypto-futures/requirement.md) · [设计](../docs/crypto-futures/design.md) · [业务架构图](../docs/crypto-futures/architecture.md) · [技术架构](../docs/crypto-futures/technical.md)
