# 技术架构：crypto-price（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求见 [requirement.md](requirement.md)；实现细节见 [design.md](design.md)。
> **仓库级**技术架构（分层/依赖/调度）见 [../architecture.md](../architecture.md)。

## 1. 模块结构

```
crypto-price/
├── scripts/
│   ├── common.py        # 薄适配层：libs 路径 + 模块目录 + 通用小工具
│   ├── sources.py       # 三源行情适配（Binance/OKX/Coinbase）+ fixtures 回放
│   ├── indicators.py    # 纯函数：多窗口涨跌/放量/OI/资金费率
│   ├── rules.py         # 纯函数：规则判定 → 事件（severity/dedupe_key/magnitude）
│   ├── delivery.py      # 去重/冷却/加剧/即时送达 → Telegram
│   ├── cards.py         # 中文告警卡片渲染
│   ├── report.py        # 每日报告
│   ├── run.py           # 编排：单实例锁 / 退出码 / 每日清理 / --once/--dry-run/--fixtures
│   ├── collect.sh       # 定时入口（60s）
│   └── verify.sh        # 模块质量门
├── config/{app,rules,watchlist}.yaml
├── tests/               # 154 项离线单测 + fixtures/（回放）
├── deploy/launchd/agent.plist.tmpl（StartInterval=60）
└── data/                # raw/ state/ reports/ exports/ logs/（gitignored）
```

## 2. 组件职责与数据契约

| 阶段 | 文件 | 输入 | 输出 |
|---|---|---|---|
| 行情 | `sources.py` | watchlist/app 配置（或 fixtures） | snapshot：三源中位数、价差、K 线、funding、OI |
| 指标 | `indicators.py` | snapshot | `window_moves` / `volume_ratio` / `oi_change_pct`（纯函数） |
| 规则 | `rules.py` | snapshot + state + rules 配置 | 事件列表（P0/P1/P2 + dedupe_key + magnitude） |
| 送达 | `delivery.py` | 事件 + delivery state | Telegram + `state/alerts-<date>.json` 审计 |
| 编排 | `run.py` | 以上 | 退出码（0/1/2/3/4）+ `state/{last_run,delivery,market_state}` + exports |

## 3. 运行链路

```
launchd(60s) → collect.sh（锁） → run.py --once
  → sources.fetch_snapshot（三源互备）
  → indicators（窗口/放量/OI）
  → rules.evaluate（阈值/点位/数据质量）
  → delivery（去重/冷却/静默/限额） → Telegram
  → state 更新 + alerts 审计 + 每日 report/exports
```

## 4. 运行与检查周期

| 项目 | 周期 / 阈值 |
|---|---|
| 运行周期 | launchd **每 60 秒**（`StartInterval=60`，与 `app.yaml` 的 `poll_seconds` 一致；不要低于 15）；单轮 2–6 秒；单实例锁，未结束→跳过（退出码 4） |
| 数据窗口 | 每轮拉一次三源快照/K 线/资金费率/OI；指标窗口 **1m / 5m / 15m / 1h** + 24h 统计 |
| 自检周期 | **每轮结束**执行 `healthcheck --quiet`（本模块不带 `--agent`）；「最近一轮未运行」阈值 **25 分钟**；同一告警 **60 分钟**内不重复 |
| 推送冷却 | 按规则：`fast_move` 15m、`volume_spike` 30m、`new_extreme`/`level_break`/`open_interest`/`data_quality` 60m、`day_move`/`funding` 120m、默认 30m；同向波动扩大 ≥1.5 倍可绕过冷却（标注「加剧」） |
| 静默 / 限额 | **22:00–07:00** 仅 P0 穿透；P1 **每日上限 10 条**；夜间被压制的 P1 合并进早报 |
| 日报 | 每天 **10:00**（）生成并推送；`app.yaml` 的 `report.send` 控制；手动 `run.sh --report` |
| 周报 | 每周日 **20:00** `crypto-analyst` 生成数据周报（launchd `--template weekly`） |
| 数据清理 | **每天首轮**执行 prune：raw 90 天 / logs 30 天 / reports 365 天 / alerts 180 天 / exports 7 天 / 单日志 2MB / `data/` 总量 500MB |
| 手工触发 | `launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-price` |


## 5. 依赖（libs/crypto_core）

`http` · `market.binance/okx/coinbase`（解析器）· `events` · `notify`（含 silent）· `store` · `timeutil` · `env/config/paths`。

## 6. 状态与数据

- `state/delivery.json`：冷却/当日计数/夜间压制/失败重试；原子写、跨重启不重复推送。
- `state/market_state.json`：24h 极值/关键位武装；`state/last_run.json`。
- `data/raw/<date>/snapshots.jsonl`：每轮快照（可回放/审计）。
- `data/exports/latest.json`：`price/snapshot/v1` 跨模块只读契约。
- 保留策略与体积上限由 `tools/prune.py` 统一处理。

## 7. 测试

- 154 项离线单测：解析器（三所）、指标、规则（含红队/对抗用例）、送达/冷却/限额、卡片、编排、报告、模拟告警。
- `fixtures/` 回放：`run.py --fixtures <dir>` 完全不联网。
- `make verify` 全绿；联网冒烟仅本地手动。

## 8. 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
| 单源失败 | 其余源继续 | **数据源分级**：价格只取 primary（Binance/OKX/Coinbase）中位数，secondary 仅对比校验不参与定价；**≥1 个主源能出价就不告警**，0 个才 rc=1 告警；消息显示「多源核验：2/3 ...（coinbase 获取异常）」 |
| 全源失败 | rc=3 | `monitoring_outage` P0 穿透静默 |
| 上一轮未结束 | rc=4 | 跳过本轮（单实例锁） |
| 休眠/漏跑 | 时间窗出现缺口 | 唤醒后继续；早报合并被压制的 P1 |
| 状态损坏 | 读取失败 | 回退空状态继续，不阻塞推送 |
