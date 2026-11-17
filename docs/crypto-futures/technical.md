# 技术架构：crypto-futures

> 本文只写模块内部实现；仓库级分层、调度、契约见 [../architecture.md](../architecture.md)。设计取舍见 [design.md](design.md)。

## 1. 代码结构

```text
crypto-futures/
├── config/
│   ├── app.yaml          # 资产、端点、轮询、历史 / 缓存 / 健康阈值
│   └── rules.yaml        # 5 条规则阈值与治理参数
├── scripts/
│   ├── run.py            # 一轮主流程 + CLI（--notify / --dry-run / --fixtures / --now / --json / --test）
│   ├── sources.py        # Fetcher：联网（crypto_core.http）/ fixtures 切片；只取已收盘周期；skip / error 分类
│   ├── series.py         # 5m 序列结构、合并、裁剪、紧凑落盘
│   ├── indicators.py     # 纯函数派生量（精确对齐变化、7 天窗口、分位、15 分钟主动买卖、合约价）
│   ├── rules.py          # 5 条规则（纯函数）
│   ├── engine.py         # 游标 + 治理（冷却 / 加剧 / 合并 / P0 上限），线上与回测共用
│   ├── reference.py      # 触发记录、补 24h 结果、历史参照统计
│   ├── cards.py          # Telegram HTML 卡片
│   ├── outputs.py        # exports（futures/snapshot/v1）、last_run.json、jsonl
│   ├── backtest.py       # 离线回放（与 run.py 共用 rules + engine）
│   ├── e2e.py            # 离线端到端（gate 自动发现）
│   ├── common.py         # libs 路径注入、目录、配置、时间、日志
│   ├── run.sh / collect.sh   # 入口；collect.sh 走 tools/lib/module_round.sh
├── tests/                # 61 个单测 + fixtures/replay（真实 5m 数据，gzip）
└── deploy/launchd/agent.plist.tmpl
```

共享库改动：`crypto_core.market.binance`（新增 `parse_long_short_ratio` / `parse_taker_ratio`，`parse_open_interest_hist` 增 `oi_usd`）、
`crypto_core.notify.templates.truncate_html`（从 crypto-price 抽出）、`crypto_core.contracts` 登记 `futures/snapshot/v1`；
`tools/healthcheck.py` 支持 `last_run.json` 的 `detail` 字段；`tools/new_module.py` 不覆盖已有文档。

## 2. 运行链路

```text
launchd（每 300s）→ collect.sh → module_round.sh（锁 / start-done 日志 / prune）
  → run.sh --notify → run.py
      ├─ load_round_state      history-*.json / engine.json / triggers.json / fetch.json
      ├─ ensure_history        冷启动 7 天 / 缺口回补（Fetcher.history，分页 500）
      ├─ fetch_recent          15 次请求：4 指标 × 3 资产 + premiumIndex × 3
      ├─ stale_metrics         按指标判过期（20 分钟）
      ├─ engine.step           游标 → rules → 治理 → cards / p2
      ├─ deliver               reference.stats → cards.format_card → send_signal（台账 + 预算）
      ├─ refresh_cache         每日：29.5 天缓存；首次 backtest.replay 预热触发记录
      └─ _persist              state / exports / last_run / raw jsonl
```

## 3. 状态与数据

| 文件 | 结构 | 大小 / 保留 |
|---|---|---|
| `state/history-<A>.json` | `{G,T,K,OI: {ts: [v1, v2]}}` 紧凑 JSON | 约 0.3 MB / 资产，7.05 天 |
| `state/cache30d-<A>.json` | 同上 | 约 1.2 MB / 资产，每天覆盖 |
| `state/engine.json` | `{cursor: {A: {rule: ts}}, last: {"rule|A|dir": [ts, mag]}, p0: {"A|date": n}}` | 只留 2 天内 |
| `state/triggers.json` | `[{ts, asset, rule, severity, direction, magnitude, price, fwd_pct, source}]` | 365 天 |
| `state/fetch.json` | 回补尝试时间、资金费率最后成功时间、缓存日期、是否已预热 | — |
| `state/last_run.json` | healthcheck 契约（`primary_ok` / `detail` / `requests` / `errors`） | 覆盖 |
| `exports/latest.json` | `futures/snapshot/v1` | 覆盖 |
| `raw/<日期>/cards.jsonl`、`p2.jsonl` | 每轮追加 | 90 天（prune） |

## 4. 性能

- 常规一轮：15 次请求 + 3 个历史文件读写，< 2 秒（网络除外）；
- 冷启动：约 60 次请求；首日 30 天缓存约 216 次请求 + 回放预热约 50 秒（只一次）；
- `backtest.py` 回放 29.5 天 × 3 币约 50 秒。

## 5. 测试与验收

见 [design.md §9](design.md#9-测试)。联网验收（2026-11-17）：`run.py --once --dry-run --json` 75 次请求、0 错误，
3 币账户比 / 大户持仓比与接口同一时间戳的值完全一致，资金费率与 `premiumIndex.lastFundingRate` 一致。
