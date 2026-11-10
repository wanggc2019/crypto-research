# 需求：crypto-price —— 主流币价格/衍生品异动实时告警

> 状态：已立项并实现（2026-11-08）｜模块目录：[`crypto-price/`](../../crypto-price/README.md)
> 文档区：本目录（需求 / 设计 / 业务架构）｜仓库技术架构：[architecture.md](../architecture.md)
> 关联：[设计文档](design.md) · [业务架构图](architecture.md) · [HANDOFF §5](../HANDOFF.md) · [约定](../conventions.md)

## 1. 要解决什么问题（一句话）

盯住 **BTC / ETH / BNB** 的现货与永续行情，在**价格剧烈波动**（急涨急跌、突破/跌破关键位、创 24h 极值、放量、资金费率/持仓量极端）时，**第一时间通过 Telegram 告警**；同时避免重复刷屏、夜间噪音和单交易所数据失真。

## 2. 边界：做什么 / 不做什么

### 做

- 多交易所**免密钥**行情：Binance（现货 + USD-M 永续）、OKX（现货）、Coinbase（现货）三源交叉核验；单源失败自动降级/回退。
- 时间窗口：`1m / 5m / 15m / 1h / 24h`（REST 轮询，默认 60 秒一轮）。
- 规则：急涨急跌、24h 大幅波动、24h 新高/新低、成交量放大、资金费率极端、持仓量（OI）异动、关键整数关口突破、多源价差异常。
- 分级 `P0/P1/P2`：P0/P1 全天即时推送（无夜间静默、无日限额）；P2 只落盘归档。
- 告警治理：去重键、按规则冷却、加剧升级、跨重启状态持久化；不做夜间压制/日限额。
- 端到端可验证：离线单测 + fixtures 回放 + 联网 `--dry-run`；`--test` 验证 Telegram 通道。
- 调度与自愈：launchd 每 60 秒跑一轮；带单实例锁；每轮内嵌健康检查与每日数据清理。
- 对外快照契约：`data/exports/latest.json`（`price/snapshot/v1`），供未来 `crypto-analyst` 消费。2026-11-09 只增 `funding_rate` / `funding_ts` / `oi` / `oi_ts` 四个字段（供 `crypto-onchain` 归因引擎用），旧消费者忽略即可。

### 不做

- **不做交易/下单/仓位管理**，不接私钥、不接交易所 API key；全部使用公开只读接口。
- **不做价格预测/机器学习**，只做可解释的规则触发。
- **不监控山寨币**：只监控 watchlist 内资产（当前 BTC/ETH/BNB；SOL/ASTER 冻结）。
- **不做 WebSocket 高频**：Python 3.9 标准库无 WebSocket 客户端，零依赖原则下用 REST 轮询；60s 级足够覆盖"剧烈波动"告警，不追求毫秒级。
- **不买数据**：CoinGlass/Glassnode/CMC Pro 等付费源不接入；CMC 免费 key 作为可选扩展位保留（默认关闭）。
- **不重复 crypto-news 的新闻/事件能力**：本模块只回答"价格发生了什么"，不解释"为什么"。

## 6. 验收标准（可测量）

1. `make verify` 全绿（ruff + libs 单测 + crypto-price 离线单测 + 端到端 fixtures）。
2. `python scripts/run.py --once --dry-run --json` 联网返回三源价格，BTC/ETH/BNB 中位数与交易所页面偏差 <0.1%。
3. `--fixtures` 回放：正常行情 0 条推送；`shock` 快照触发 `fast_move` P0 且卡片含现价/各窗口涨跌/多源核验。
4. 冷却生效：同一事件连续两轮只推一次；幅度扩大 1.5 倍可再推。
5. 即时送达：模拟 23:00 的 P0/P1 都立即推送，不进入早报压制。
6. 单源失败：OKX/Coinbase 挂掉仍能出价（Binance 主源），全挂时 `monitoring_outage` 强制送达、退出码 3。
7. `--test` 能收到 Telegram 测试消息；launchd 安装后 `collect.log` 每 60s 有 start/done（rc=0）。
8. 运行 10 轮无重复告警、无异常日志、`data/` 不失控增长。

## 7. 依赖的共享能力

- 已有：`paths/env/config/store/timeutil/text`、`events.Alert`、`notify`、`http`、`market.binance`。
- 新增（P4 允许）：`market.okx`、`market.coinbase`、`market.binance.parse_tickers24h/parse_klines/parse_open_interest`；`notify.send_message(silent=)`。
- 策略/阈值/路由全部留在模块 `config/`，不进 libs。

## 8. 更多

- 数据源、告警策略、产物结构、接口契约、风险与对策 → [design.md](design.md)
- 业务能力/数据流/告警生命周期图 → [architecture.md](architecture.md)

---
> 文档维护约定见 [docs/README.md](../README.md)。
