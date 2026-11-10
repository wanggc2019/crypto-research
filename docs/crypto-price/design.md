# 设计：crypto-price —— 主流币价格/衍生品异动实时告警

> 本文是 crypto-price 的**实现设计**（数据源、规则、产出、接口契约、风险）。
> 需求与验收见 [requirement.md](requirement.md)；业务架构图见 [architecture.md](architecture.md)。
> 模块代码：[`crypto-price/`](../../crypto-price/README.md)

## 1. 数据源评估

| 源 | 接口 | 能力 | 密钥 | 限流 | 结论 |
|---|---|---|---|---|---|
| Binance 现货 | `api.binance.com/api/v3/ticker/24hr`、`/klines` | 最新价、24h OHLC、成交量、1m K 线 | 免 | 6000 weight/min | **主源**（K 线/24h 统计最完整） |
| Binance 永续 | `fapi.binance.com/fapi/v1/premiumIndex`、`/openInterest` | 资金费率、标记价、持仓量 | 免 | 2400 weight/min | **衍生品唯一源** |
| OKX 现货 | `www.okx.com/api/v5/market/ticker`、`/candles` | 最新价、24h OHLC、成交量、1m K 线 | 免 | 20 req/2s | 第二源 + K 线回退 |
| Coinbase 现货 | `api.exchange.coinbase.com/products/{id}/stats` | 最新价、24h OHLC、成交量 | 免 | 10 req/s | 第三源（价差核验） |
| CMC | `pro-api.coinmarketcap.com` | 汇总价 | 需 key、免费额度低、分钟级 | — | 可选，默认关闭 |
| 其他（CoinGecko/CoinCap） | 聚合 | 汇总价 | 免/限流 | 严格 | 不采用，避免成为单点 |

**取舍**：不把任何单一交易所当唯一真相。用三源中位数作为"市场价"，价差超阈值时**抑制价格类告警**并提示数据异常；Binance 失败时 OKX 接管 K 线/统计；三源全挂时发 `monitoring_outage`（穿透静默）而不是静默失败。

## 2. 告警策略

### 4.1 规则与分级（阈值全部在 `config/rules.yaml` / `config/watchlist.yaml`）

| 规则 | 触发（默认，按资产可调） | P1 | P0 |
|---|---|---|---|
| `fast_move` | 1m/5m/15m/1h 涨跌幅 | 达到各窗口阈值（BTC 0.6/1.0/1.5/2.0%） | 达到 `max(阈值×2, 3%)` |
| `day_move` | 24h 涨跌幅 | BTC 5% / ETH 6% / BNB 7% | BTC 10% / ETH 11% / BNB 12% |
| `new_extreme` | 24h 新高/新低（超出前值 ≥5bps） | 默认 P1 | 同时 1h 同向 ≥2.5% |
| `volume_spike` | 近 5m 成交量 / 前 55m 均值 | ≥3 倍且 5m 价格 ≥0.4% | ≥6 倍且 5m 价格 ≥1.0% |
| `funding` | 永续 8h 资金费率绝对值 | ≥0.10% | ≥0.20% |
| `open_interest` | 15m 持仓量变化（需同向价格 ≥0.5%） | ≥5% | ≥10% |
| `level_break` | 穿越 watchlist 关键整数位（确认 3bps） | P1 | — |
| `data_quality` | 三源价差 >50bps / 主源 0 个 / 行情过期 | 数据异常提示 | 全源不可用 → P0 穿透静默；**多资产同时异常合并为一条** |

> 同一轮里 `fast_move` 多窗口同时触发只发**最强的一条**，避免 1m/5m/15m 三连刷屏；同向波动继续扩大 ≥1.5 倍时可绕过冷却再报一次（标题带"加剧"）。

### 4.2 分级与送达

- **P0**：立即推送（价格剧烈波动任何时候都要知道）。
- **P1**：立即推送；**不设夜间静默、不设日限额**，仅按规则冷却/去重。
- **P2**：只写 `state/alerts-<date>.json` 归档，不推送（避免噪音）。
- 日报（每天 10:00 ，时间可配）：过去 24h 行情快照 + 当日告警摘要；每天最多一次；发送失败 10 分钟后重试。
- 周报（每周日 20:00 ，独立 `crypto-analyst` 模块）：只读汇总 price/news 的 exports，生成数据周报；20:05 DSH 自动化任务做 AI 复盘。
- 去重键形如 `price:BTC:fast_move:down`、`price:ETH:level:2500:up`；冷却按规则（15–120 分钟）可配。

### 4.3 安静与可靠

- 无夜间静默：`quiet_hours: "off"`，P0/P1 全天即时发送；`monitoring_outage` 强制送达。
- 单实例锁 `data/state/collect.lock`，上一轮未结束则跳过（退出码 4）。
- 状态文件原子写；跨重启不重复推送；全源失败退出码 3，部分失败 1。
- 每轮内嵌 `tools/healthcheck.py`（含自愈/Telegram 告警）和每日 `tools/prune.py`。

## 3. 产物

```
crypto-price/
├── config/{watchlist,rules,app}.yaml
├── scripts/{run,sources,indicators,rules,delivery,cards,report,common}.py + *.sh
├── tests/（离线单测 + fixtures 回放）
├── deploy/launchd/agent.plist.tmpl（StartInterval=60）
└── data/（gitignored）
    ├── raw/<date>/snapshots.jsonl    # 每轮行情快照（可回放/审计）
    ├── state/delivery.json            # 冷却/当日计数/夜间压制/失败重试
    ├── state/{market_state,report,last_run}.json
    ├── state/alerts-<date>.json       # 每轮判定审计（含未推送）
    ├── reports/<date>.md              # 每日报告
    ├── exports/latest.json            # 跨模块只读契约
    └── logs/{price.log,collect.log}
```

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| REST 60s 采样漏掉瞬时插针 | 用 1m K 线回看窗口；P0 阈值留余量；可把轮询降到 30s |
| 交易所维护/限流 | 三源互备 + 退避重试 + `monitoring_outage` 告警 |
| 假突破/噪音刷屏 | 新极值 5bps 滞后、关键位确认 3bps、按规则冷却、每日限额 |
| Mac 休眠导致漏报 | launchd 唤醒后续跑；早报补齐；Runbook 给出 VPS 后路 |
| 夜间告警打扰 | 仅 P0 穿透；P1 汇总早报 |
| 状态损坏 | 原子写 + 容错读（坏文件回退空状态） |

## 5. 模块内部接口契约（实现约定）

> 目的：让「行情源适配」「指标+规则引擎」「送达/编排」三条工作线可以并行开发、互不踩踏。
> 所有价格 = `float`，所有时间 = **epoch 秒 int**（适配器内部把毫秒转成秒），K 线一律**旧→新**排序。

### 9.1 libs 行情适配器（`libs/crypto_core/src/crypto_core/market/`）

| 函数 | 返回 |
|---|---|
| `binance.parse_tickers24h(raw)` | `list[dict]`：`symbol,last,open,high,low,volume,quote_volume,change_pct,ts` |
| `binance.parse_klines(raw)` | `list[dict]` 旧→新：`open_time,open,high,low,close,volume,close_time` |
| `binance.parse_open_interest(raw)` | `dict`：`symbol,oi,ts` |
| `binance.parse_funding(raw)` | 保持原样 `symbol,rate,mark_price`（可加 `ts`，不得破坏现有测试） |
| `okx.parse_ticker(raw)` | `dict`：`symbol,last,open,high,low,volume,quote_volume,change_pct,ts` |
| `okx.parse_candles(raw)` | `list[dict]` 旧→新：`open_time,open,high,low,close,volume,quote_volume,close_time,confirm` |
| `coinbase.parse_stats(raw, symbol="")` | `dict`：`symbol,last,open,high,low,volume,change_pct,ts` |
| `coinbase.parse_ticker(raw, symbol="")` | `dict`：`symbol,last,bid,ask,volume,ts` |

解析失败返回 `None`（单条）或 `[]`（列表），不得抛异常给调用方；所有测试用内联样本，不联网。

### 9.2 `crypto-price/scripts/sources.py`

```python
def fetch_snapshot(watchlist, app_cfg, fixture_dir=None, now_ts=None) -> dict
```

返回结构：

```python
{
  "ts": "2026-11-08T03:40:00", "ts_epoch": 1794096000,
  "quotes": {"BTC": {"price": 75790.5,           # 三源中位数
                      "sources": {"binance": {"price": 75790.54, "ts": 1794096000},
                                  "okx": {...}, "coinbase": {...}},
                      "spread_bps": 3.1, "n_sources": 3, "agree": True}},
  "stats": {"BTC": {"high": 76251.1, "low": 75569.1, "open": 75928.4,
                     "change_pct": -0.184, "volume": 2055.9,
                     "source": "binance", "sources": {...}}},
  "candles": {"BTC": {"source": "binance", "rows": [...], "errors": []}},
  "funding": {"BTC": {"rate": 0.00005475, "mark_price": 75756.0, "ts": 1794096000}},
  "oi": {"BTC": {"oi": 95982.052, "ts": 1794096000}},
  "errors": [{"source": "okx", "asset": "ETH", "error": "TimeoutError"}],
  "sources_ok": 8, "sources_total": 9,
}
```

- 源优先级：`stats`/`candles` 用 Binance → OKX → Coinbase；`funding`/`oi` 只有 Binance 永续。
- `fixture_dir` 存在时**完全不联网**，按 `<fixture>/<source>/<kind>_<ASSET>.json` 读原始响应：
  `binance/ticker24h_BTC.json`、`binance/klines_BTC.json`、`binance/premiumIndex_BTC.json`、
  `binance/openInterest_BTC.json`、`okx/ticker_BTC.json`、`okx/candles_BTC.json`、
  `coinbase/stats_BTC.json`。缺文件记为 error，继续跑（用于故障切换测试）。
- 任何单源失败都不能抛出；返回的 `errors` 交给 run.py 决定退出码。

### 9.3 `crypto-price/scripts/indicators.py`（纯函数）

```python
def pct_change(price, reference) -> float | None
def window_moves(candles, price, now_ts, windows) -> dict   # {"m1": -0.8, "m5": -2.1, ...}
def volume_ratio(candles, now_ts, window_min=5, baseline_min=55) -> dict | None
# -> {"ratio": 4.2, "window_volume": 12.3, "baseline_avg": 2.9, "window_min": 5}
def oi_change_pct(history, now_ts, window_min=15) -> float | None
# history: [{"ts": epoch, "oi": float}, ...] 旧→新，找不到足够旧样本返回 None
```

- `window_moves`：用 `close_time <= now_ts - window` 的最后一根**已完成** K 线收盘价作参照；
  样本不足返回 `None`（调用方的规则跳过该窗口）。
- `volume_ratio`：最近 `window_min` 根已完成 K 线成交量之和 / window_min，对比之前
  `baseline_min` 根的每根均值；比率 = 近端每分钟均量 / 基线每分钟均量。
- 边界：除零、负值、空列表返回 `None`，不抛异常。

### 9.4 `crypto-price/scripts/rules.py`（纯函数）

```python
def evaluate(snapshot, state, rules_cfg, watchlist, now=None) -> list[dict]
def advance_state(state, snapshot, now=None) -> dict   # 返回新 state，不修改入参
```

事件 dict（送达到 `delivery.py`）：

```python
{
  "rule": "fast_move",              # fast_move/day_move/new_extreme/volume_spike/funding/open_interest/level_break/data_quality
  "asset": "BTC",
  "direction": "down",              # up/down/flat
  "severity": "P0",                 # P0/P1/P2
  "title": "BTC 5 分钟急跌 2.10%，现价 75,412",
  "detail": ["5m -2.10% · 15m -2.8% · 1h -3.4% · 24h -6.1%", "24h 区间 75,208 – 78,878", ...],
  "metrics": {"window": "m5", "change_pct": -2.1, "price": 75412.0, "spread_bps": 3.1, ...},
  "dedupe_key": "price:BTC:fast_move:down",
  "magnitude": 2.1,                 # 用于冷却期"加剧"判断（同单位、可比较）
  "urls": ["https://www.binance.com/zh-CN/trade/BTC_USDT?type=spot"],
  "why": "急跌通常伴随杠杆清算，波动可能继续放大",
  "action": "检查杠杆仓位与止损；不要在插针时追单（不构成投资建议）",
  "force_send": False,              # True = 无视静默时段（仅 monitoring_outage 用）
  "ts": "2026-11-08T03:40:00",
}
```

- `state`：`{"extremes": {"BTC": {"high": 76251.1, "low": 75569.1, "ts": 1794096000}},`
  `"last_prices": {"BTC": {"price": 75790.5, "ts": 1794096000}},`
  `"oi_history": {"BTC": [{"ts": 1794096000, "oi": 95982.0}, ...]}}`
- 数据质量不达标（`spread_bps > max_spread_bps_p1` 或 `n_sources < min_sources`）时：
  跳过价格类规则（fast_move/day_move/new_extreme/volume_spike/level_break），
  但仍评估 funding/open_interest，并产出一条 `data_quality`（P1）。
- 同一资产同一轮 `fast_move` 多窗口只产出一条（取最严重/超阈值倍数最大者）；同规则同资产同方向
  多事件去重键相同，`delivery` 会再兜底。
- 阈值全部从 `rules_cfg` 读，不在代码里硬编码；资产阈值缺失时用 `default` 或跳过并记 warning。

---
> 实现约定：模块内 `scripts/` 按 sources → indicators → rules → delivery 分层，纯函数可离线测试；
> 阈值全部在 `crypto-price/config/*.yaml`，共享能力在 `libs/crypto_core`。
