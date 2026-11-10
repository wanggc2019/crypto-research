# 技术架构：crypto-onchain（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求与边界见 [requirement.md](requirement.md)；数据源/公式/归因规则/候选结构/导出契约见 [design.md](design.md)。
> **仓库级**技术架构（分层/依赖/调度）见 [../architecture.md](../architecture.md)；仓库约定见 [../conventions.md](../conventions.md)。
> 当前为 **M2 影子模式（4 类卖压候选）+ M3 附加能力（walletflow 与事件驱动告警）**：采集 + 指标 + 阈值 + **归因（含跨轮 OI 历史）** + 影子落盘 + **`onchain/pressure/v1` 导出** + **链上大额转账（公共 RPC）** + **每轮新增 ≥$10M 的 Telegram 告警（仅 P0 推送、全天即时（P1 不推送，仍落盘/进摘要）；特殊期解除日限额；消息含数量/区块/标签/背景/交易链接）**；4 类卖压候选仍不推送（launchd 影子任务已在跑）。

## 1. 模块结构

```
crypto-onchain/
├── README.md
├── config/
│   ├── app.yaml            # 端点、回补窗口/翻页、超时、影子产物、exports 路径、轮询周期
│   ├── rules.yaml          # 4 类信号阈值/分级 + 归因参数（attribution.*）+ 影子校准 + M3 送达骨架
│   └── watchlist.yaml      # BTC/ETH/BNB 交易对、OKX uly、ctVal、关注 CEX
├── scripts/
│   ├── common.py           # 薄适配层：libs 路径、目录、配置、时间/存储、price exports 只读
│   ├── sources.py          # 取数 + fixtures 回放；解析全部委托 libs
│   ├── indicators.py       # 纯指标：taker / cex / stablecoin / liquidation / oi_change_pct
│   ├── signals.py          # 阈值判定 + 方向对齐 → 候选
│   ├── attribution.py      # M2 归因引擎（纯函数）：4 类类别 + evidence/counter_evidence/unknown
│   ├── run.py              # 编排：CLI / 采集 / OI 历史 / 归因 / 快照 / 候选 / exports / walletflow 告警 / 退出码
│   ├── digest.py           # 每日影跑摘要（只汇总不告警；新增“今日链上告警”统计行；--if-due 由 collect.sh 调用）
│   ├── walletflow.py       # M3 附加：链上大额转账（公共 RPC eth_getLogs；解码/阈值/去重/可选标签方向）
│   ├── walletflow_alerts.py# 事件驱动告警：P0/P1、全天即时、日限额/扩容、特殊期解除限额、聚合、pending、推测备注、消息字段/标签补全（含“流向+方向合并行”，Etherscan 页面 name tag，缓存 30 天）、dry-run
│   ├── run.sh              # 一键入口（venv 回退 python3）
│   ├── collect.sh          # launchd 入口：锁 → run.sh → healthcheck → 当天首轮 prune → rc 透传
│   └── verify.sh           # 质量门：ruff + libs 单测 + 模块单测 + fixtures e2e
├── tests/
│   ├── test_indicators.py  # 18 项
│   ├── test_signals.py     # 22 项
│   ├── test_attribution.py # 15 项（M2 新增）
│   ├── test_digest.py      # 7 项
│   ├── test_walletflow.py  # 12 项（M3 附加）
│   ├── test_walletflow_alerts.py # 21 项（事件驱动告警 + 标签补全）
│   ├── test_run_fixtures.py# 5 项（端到端）
│   └── fixtures/           # 3 个 binance_*.json + 3 个 okx_liq_*.json
│                           # + walletflow_logs_ethereum.json / walletflow_logs_bsc.json
│                           # + cexs.json + stablecoins.json + price_export.json（含 funding_rate/oi）
├── deploy/launchd/agent.plist.tmpl   # 模板（__INTERVAL__ 由 tools/render_launchd.py 填）
└── data/                   # gitignored；由 store 层按需创建父目录（--dry-run/--status 不建）
    ├── raw/                # 按日：attributions.jsonl / walletflow.jsonl
    ├── state/              # shadow-latest.json / shadow-candidates.jsonl / oi-history.json
    │                       # / walletflow-cursor.json / walletflow-alerts.json / address-tags.json
    ├── reports/            # 未使用
    ├── exports/            # onchain-pressure.json（onchain/pressure/v1）
    └── logs/               # onchain.log + collect.log（start/done，看门狗据此判定）
```

## 2. 组件职责

| 文件 | 职责 | 关键入口 | 约束 |
|---|---|---|---|
| `common.py` | 仓库根探测并注入 `libs/crypto_core/src`；固定 `CONFIG_DIR`/`DATA_DIR`；三份 YAML 缓存加载与 `schema_version` 校验；时间/时区；JSON/JSONL 读写包装；`onchain.log` 追加；`crypto-price` exports 只读加载（含 `funding_rate`/`oi`） | `load_all_configs`、`ensure_dirs`、`apply_timezone`、`load_price_context`、`write_json`、`log_line` | 不 import 业务模块，保持薄 |
| `sources.py` | HTTP 取数与 fixtures 读盘，统一返回 `(data, error)`；不判阈值、不算指标 | `fetch_binance_trades`、`fetch_cex_flows`、`fetch_stablecoins`、`fetch_okx_liquidations`、`load_fixture_*` | 单源异常全部降级为 `error` 字符串；解析只调用 `libs` |
| `indicators.py` | 纯计算：`direction_of`、`oi_change_pct`、`taker_stats`、`cex_flow_metrics`、`stablecoin_metrics`、`liquidation_metrics` | 同名函数 | 不联网、不写盘；缺失一律 `None` 而非 0 |
| `signals.py` | 把指标 + `rules.yaml` 变成候选；计算 `aligned`/`would_alert`；按 P0→P1 排序 | `eval_taker`、`eval_cex_flow`、`eval_liquidation`、`eval_stablecoin`、`build_candidates` | 阈值只从 rules 传参读取；`note` 禁止主体指认 |
| `attribution.py` | **M2 归因引擎**：把资产快照 + CEX/稳定币资金流 + 跨轮 OI 变化组合成 4 类卖压结论（资产级 + 市场级），每条固定 `evidence`/`counter_evidence`/`unknown`；**永不输出身份** | `attribute_asset`、`attribute_market`、`build_attributions` | 纯函数：不联网、不写盘；阈值从 rules 读取（资产级 + 市场级均读配置）；无身份字段 |
| `walletflow.py` | **M3 附加**：公共 RPC `eth_getLogs` 增量扫 USDT/USDC 的 Transfer 日志 → 解码/阈值过滤（`walletflow.min_usd=10M`、BSC 链级 5M）/去重/内置验证标签方向（cex_in/cex_out/cex_internal）；返回 `(records, meta, error)`；**数据记录不识别地址归属** | `fetch_transfers`、`select_and_classify`、`decode_transfer`、`classify_direction`、`pending_range`、`scan_logs`、`summarize`、`fixture_records` | 标准库 `urllib` 直连 RPC（不用 libs `http`）；失败返回 error 不抛；`--fixtures` 复用同一套解码/过滤/去重逻辑 |
| `context.py` | **智能判断**：把价格（assets/signals）、crypto-news 新闻、CEX/稳定币资金面融合成 0–N 分；`assess()` 决定是否打扰（巨鲸始终发），输出 `{send, score, reasons, whale}`；`load_news()`/`load_price_export()` 只读跨模块 exports | `assess`、`load_news`、`load_price_export`、`_recent_news`、`_keyword_hit` | 纯函数、离线可测；新闻缺失/损坏降级为无新闻，不阻塞告警 |
| `walletflow_alerts.py` | **事件驱动告警**：从本轮记录挑 ≥`alerts.min_usd` 且未发过的转账 → 分级（P0 单笔 ≥$50M 或 `cex_in` ≥$25M）→ 发送时段（`quiet_hours` 默认空 = 全天即时；非空时仅 P1 抑制）→ 特殊期判定（命中任一 → 常规 10 条解除、特殊期硬上限 30 条/天）→ 常规日限额（only_p0 时 10 条/天；巨鲸 ≥$100M 或入所 ≥$50M 额外 5 条/天，标「🐋 巨鲸」）→ 单轮 ≤5 笔 → 同 tx 多腿只留 USD 最大一腿 → **真实发送前 `enrich_tags` 补地址标签**（内置优先；其次抓 Etherscan 地址页 name tag，仅 ETH，缓存 `state/address-tags.json` 30 天，失败静默）→ 同交易对聚合渲染（“（N 笔）”；含数量/区块/标签/方向/背景/交易链接）→ Telegram 发送 → 写状态；`unknown` 方向可带**明确标注的“推测”**备注 | `process`、`select_alerts`、`render_message`、`severity_of`、`daily_cap`、`special_mode`、`in_quiet_hours`、`enrich_tags`、`parse_etherscan_tag`、`fetch_etherscan_tag`、`_fmt_amount`、`_tx_url`、`build_history`、`guess_counterparty`、`load_state` / `save_state` | 纯本地判断 + `crypto_core.notify` 发送；发送成功才计入状态；失败进 `pending`（≤20）；dry-run 只预览不写状态、**不联网补标签**；异常由 `run.py` 捕获，不改 rc |
| `run.py` | 参数解析、配置校验、采集编排、**OI 历史（官方优先 + 本地兜底）**、**归因调用**、**归因日志追加**、候选补归因、**walletflow 编排（游标/调用/摘要/明细）**、**walletflow 告警调用**、快照/候选落盘、**exports 写入**、日志、`--status`、退出码 | `main`、`run_once`、`_collect`、`_oi_changes`、`_load_walletflow_cursor`、`_advance_walletflow_cursor`、`_log_walletflow`、`_export_payload`、`_write_exports`、`_write_shadow`、`_status` | 唯一写盘入口；dry-run 不写任何产物（含 OI 历史、walletflow 游标/明细、告警状态与 exports） |
| `run.sh` / `collect.sh` | 统一走 `.venv/bin/python`（不存在回退 `python3`），`PYTHONDONTWRITEBYTECODE=1`；`collect.sh` 是 launchd 稳定入口：`mkdir` 单实例锁 + start/done 日志 + healthcheck（异常才告警/自愈）+ 当天首轮 prune + rc 透传 | — | 不解析参数、不复制业务逻辑 |
| `digest.py` | 每日影跑摘要：读快照/候选/归因日志/**walletflow 告警状态** → 中文摘要（Telegram HTML，含“今日链上告警”行）→ 到点且当日未发才发；**不是告警通道** | `build_summary`、`is_due`、`send_digest`、`main` | 只读本模块产物；`--dry-run` 不写状态不发送；发送成功才写 `state/digest.json` |
| `verify.sh` | 质量门：ruff → `libs` 单测 → 模块 150 项 → fixtures e2e；`--with-network` 追加联网冒烟 | — | 默认不联网 |

## 3. 一次运行的调用链

```
main(argv)                                      # scripts/run.py
├─ argparse：--dry-run / --json / --fixtures / --data-dir / --status
├─ common.load_all_configs()                    # watchlist.yaml → rules.yaml → app.yaml（带缓存）
├─ common.apply_timezone(app.timezone)          # 默认 本地时区；设置 TZ + tzset
├─ common.schema_problem(每份配置, 1)            # 不符 → stderr + rc=2
├─ common.data_paths(args.data_dir)              # 只算路径（不建目录；写入时由 store 建父目录）
├─ if --status: _status(cfg, paths)             # 只读快照/候选，不联网 → rc 0/1
└─ run_once(cfg, paths, fixtures_dir, dry_run)
   ├─ _collect(...)                             # 采集 + 指标（同 M1，另把 oi/funding_rate 放进资产快照）
   │  ├─ 时钟：联网模式 now_ts=time.time()；fixtures 模式先置空、等成交回来再定
   │  ├─ 逐笔：fixtures → load_fixture_trades；联网 → fetch_binance_trades(...)
   │  ├─ fixtures 时钟：now_ts = max(各资产 fixture 的 last_ts)
   │  ├─ 行情背景：fixtures → _price_context_offline；联网 → common.load_price_context(app, now_ts)
   │  │             （读取 price/change_24h_pct/funding_rate/oi；过期时 direction 强制 unknown）
   │  ├─ CEX/稳定币/清算：fixtures 或联网取数
   │  ├─ indicators.cex_flow_metrics / stablecoin_metrics
   │  └─ 逐资产 _asset_snapshot：taker 5m/15m + liquidation_metrics + oi/funding_rate
   ├─ directions = {asset: direction_of(change_24h_pct)}
   ├─ signals.build_candidates(asset_metrics, cex_flows, stablecoins, rules, directions)
   ├─ 链上大额转账（walletflow，M3 附加；失败不阻塞）
   │  ├─ cursor = _load_walletflow_cursor(paths)       # data/state/walletflow-cursor.json
   │  ├─ fixtures → walletflow.fixture_records；联网 → walletflow.fetch_transfers(wf_cfg, cursor)
   │  ├─ walletflow.summarize(records, meta, error) → snapshot["walletflow"]
   │  ├─ if not dry_run and not error：_advance_walletflow_cursor + _log_walletflow
   │  │                                             # raw/<date>/walletflow.jsonl（告警前落盘，供当天笔数扩容）
   │  └─ 事件驱动告警（if alerts.enabled and not fixtures_dir；异常只记 error）
   │     ├─ walletflow_alerts.process(wf_records, alerts_cfg, paths, labels, dry_run, now, context)
   │     │  ├─ select_alerts（分级 / 限额 / 特殊期 / 去重 / 同 tx 多腿 / 单轮上限）
   │     │  ├─ tags = {} if dry_run else enrich_tags(to_send, paths, alerts_cfg)
   │     │  │        # 内置 labels 优先；Etherscan 地址页 name tag（仅 ETH；缓存 state/address-tags.json 30 天）
   │     │  ├─ render_message（标题 / 数量·区块 / from→to / 标签 / 方向 / 背景 / 交易链接）
   │     │  └─ Telegram 发送（dry-run 不发）→ 成功写 state/walletflow-alerts.json
   │     ├─ snapshot.walletflow["alerts"] = {sent_round, suppressed, error}
   │     └─ if dry_run and text：snapshot.walletflow["alerts_preview"] = text（仅内存/stdout，不落盘）
   ├─ 官方 OI 历史：fixtures → load_fixture_oi_history；联网 → fetch_oi_history(futures_base, symbol, 15m, 3)
   ├─ _oi_changes(assets, paths, now_ts, dry_run, rules, oi_hist)
   │  ├─ 优先 indicators.oi_change_pct(官方点, tolerance=20m) → oi_source="binance_hist"
   │  ├─ 兜底 _load_oi_history：data/state/oi-history.json → 追加本轮 oi → 截断 400 点
   │  ├─ indicators.oi_change_pct(本地点) → oi_source="local"
   │  └─ if not dry_run: common.write_json(oi-history.json)（OSError 静默，官方来源时仍累积兜底）
   ├─ 每个资产写 row["oi_change_15m_pct"] / row["oi_source"]
   ├─ attribution.build_attributions(assets, cex_flows, stablecoins, rules, directions, oi_changes)
   │  ├─ 逐资产 attribute_asset（价格门控 → 4 类判定 → 三段对象或 None）
   │  └─ attribute_market（BTC down + CEX/稳定币资金流 → 仅 capital_exit 或无）
   ├─ 候选补 attribution 摘要 {category, category_code, confidence}（命中才写）
   ├─ 组装 snapshot（schema/source_module/mode/now_ts/assets/cex_flows/stablecoins/
   │               price_context/candidates/attribution/walletflow/source_health/errors）
   ├─ _candidate_records(candidates, now_ts, assets, cex_flows)   # jsonl 行（含 attribution）
   ├─ if not dry_run:
   │  ├─ _write_shadow(...)                     # state/shadow-latest.json + candidates.jsonl
   │  ├─ _write_exports(cfg, paths, snapshot)   # data/exports/onchain-pressure.json（onchain/pressure/v1）
   │  └─ common.log_line("shadow: ... attributions=N errors=N")
   └─ 退出码判定 → (snapshot, records, written, rc, elapsed)
└─ snapshot["elapsed_seconds"] = elapsed        # 仅 stdout 输出；落盘快照在 run_once 内已写，不含该字段
   └─ 打印 JSON（--json）或中文摘要（含归因行与导出路径）；return rc
```

要点：

- 调用顺序是"**采集 → 指标 → 候选 → walletflow（记录 + 事件告警）→ OI 历史 → 归因 → 补候选归因 → 影子 + exports → 退出码**"。候选先生成，归因随后计算并**回填**到候选（只加摘要），不改变候选的产生与分级；walletflow（含告警）与归因互不影响。
- `run_once` 是唯一闭环；`--dry-run` 只跳过影子、exports、日志与**告警发送/告警状态**写入，**仍会完整计算归因并输出快照**（含 `attribution` 与 `oi_change_15m_pct`），但**不会**创建/更新 `oi-history.json`、exports 或 `walletflow-alerts.json`；告警只在 `alerts_preview` 里预览。
- `elapsed_seconds` 在落盘之后才补进内存快照，所以 `shadow-latest.json` 里没有它，而 `--json` 的 stdout 里有。
- 失败隔离：任何单源失败只进 `errors[]`；OI 历史写入失败被吞掉；walletflow 的 RPC 失败**只写快照 `walletflow.error`**（不进 `errors[]`、不改变 rc），游标不前进；walletflow 告警的凭据/发送/代码异常只写快照 `walletflow.alerts.error`、记录留 `pending`，同样不改 rc；但快照/候选/exports 的写盘异常会向上抛出（本轮非零退出，由 `collect.sh`/healthcheck 暴露）。

## 4. 配置加载与时钟

### 4.1 配置

- 三份配置固定为 `watchlist.yaml`、`rules.yaml`、`app.yaml`（`common.CONFIG_FILES`），首次读取后缓存（`_CFG_CACHE`），同一进程内改文件不会重载。
- 每份配置必须有 `schema_version: 1`；任一不符即 `rc=2`，不进入采集。
- `app.yaml` 的 `mode` 会被校验（只支持 `shadow`，其它值直接 rc=2）；`sources.{binance,okx,defillama}.enabled=false` 会跳过对应采集（不报错）；`poll_seconds` 由 `tools/render_launchd.py` 渲染到 launchd 模板。`shadow.enabled` 控制是否写快照/候选。
- `app.yaml` 的 `exports.file` / `exports.schema` 由 `_write_exports` 读取（产出路径与契约名）；`--status` 里展示的 exports 路径是"data 根 + 默认文件名"的拼接，不读该配置项。
- 归因参数（`attribution.enabled`、`oi_significant_pct`、`oi_window_minutes`、`oi_history_hours`）由 `attribution.py` 与 `_oi_changes` 读取；`delivery.*` 仍未被读取（M3，4 类卖压候选）。
- `app.yaml` 的 `walletflow.*`（`enabled`、`min_usd`、`include_mint_burn`、`timeout_seconds`、`alerts.only_p0`、`alerts.enrich`、`labels`、`chains[]`（`name`/`rpc_url`/`min_usd`/`scan_blocks_init`/`max_blocks_per_round`/`chunk_blocks`/`tokens`））由 `run.py` 读取后传给 `walletflow.py`；`labels` 已内置 23 个已验证地址（21 cex / 1 mm / 1 issuer）（官方清单 + Etherscan name tag；`type: cex`/`mm`），`ETHERSCAN_API_KEY` 未被读取。
- `walletflow.alerts.*`（`enabled`、`min_usd`、`p0_usd`、`p0_cex_in_usd`、`daily_cap`、`whale_usd`、`whale_cex_in_usd`、`whale_daily_cap`、`burst_trigger_ratio`、`burst_cap`、`max_per_round`、`quiet_hours`、`guess`、`special`、`enrich`、`rare_only`、`rare`）由 `run.py` 把 `alerts` 段整体传给 `walletflow_alerts.process`；`quiet_hours` 默认 `""`（全天即时），`special` 含 `enabled`/`manual`/`p0_transfers_today`/`btc_move_24h_pct`/`cex_outflow_bps`/`stablecoin_outflow_usd`，特殊期由 `run.py` 把 BTC 24h 涨跌幅、CEX 净流 bps、稳定币净增发作为 `context` 传给 `process`。告警阈值与记录阈值独立（BSC 记录 $5M、告警仍 $10M）。`digest.py` 另读 `alerts.daily_cap` 用于摘要“常规上限”文案。
- 告警补标签配置 `walletflow.alerts.enrich`（`etherscan_tags`、`timeout_seconds`、`cache_days`）由 `walletflow_alerts.enrich_tags` 自己从 `alerts_cfg` 读取；仅**真实发送**时调用，`--dry-run` 不调用（不联网），因此预览里只有内置标签。`ETHERSCAN_API_KEY` 仍未被读取（页面抓取不需要 key）。
- `delivery.*` 仍未被读取（仅 4 类卖压候选的 M3 规划）；walletflow 告警不使用它。
- 阈值全部从 `rules.yaml` 读入后作为参数传给 `signals.*` / `attribution.*`；函数内默认值仅作键缺失兜底。

### 4.2 时钟（尤其是 `--fixtures`）

- **联网模式**：进入 `_collect` 即 `now_ts = int(time.time())`，所有窗口（5m/15m/清算 15m/OI 15m）都相对它计算。
- **fixtures 模式**：先读全部资产的逐笔 fixture，再用 `max(各资产 last_ts)` 作为本轮 `now_ts`；若没有任何成交，退回 `time.time()`。这是为了保证窗口过滤不会把回放样本全部丢掉——**fixtures 的"现在"由数据决定，而不是运行时刻**。e2e 测试据此把 OI 历史点种在 `FIXTURE_NOW - 1000`。
- 窗口过滤对交易所时钟偏差留了余量：`taker_stats` 接受 `ts ≤ now_ts + 120` 的成交；`liquidation_metrics` 只按 `ts ≥ now_ts - window` 过滤（无上界）。
- 行情背景新鲜度：联网模式用 `generated_at` 与 `now_ts` 比较，超过 `sources.price_exports.max_age_seconds`（900 秒）置 `fresh=false`；fixtures 模式解析 `price_export.json` 时**无条件** `fresh=true`，不检查时效。
- OI 历史窗口按 `now_ts` 与 `attribution.oi_history_hours` 裁剪；`oi_change_pct` 的容差是 10 分钟，轮询中断过久会让 15m 变化返回 `None`。
- `generated_at`/日志时间使用 `apply_timezone` 后的本地时区（默认 `本地时区`）。

## 5. CLI 语义

| 参数 | 语义 | 副作用 | 输出 |
|---|---|---|---|
| `--dry-run` | 完整跑一轮，只预览 | 不写快照/候选/日志/exports/**OI 历史/walletflow 告警状态**，**也不创建任何目录**；walletflow 告警只生成预览（`walletflow.alerts_preview`） | 与普通轮次相同的中文摘要或 JSON（JSON 含 `elapsed_seconds`、`attribution`、`walletflow.alerts_preview`） |
| `--json` | 机器可读输出 | 无 | 普通轮次输出完整 `snapshot`；与 `--status` 同用时输出状态对象 |
| `--fixtures DIR` | 离线回放：逐笔/CEX/稳定币/清算/行情全部读 `DIR` 下固定文件名，完全不联网；**walletflow 告警不调用**（不发送、不预览） | 同普通轮次（dry-run 时不落盘）；时钟取 `max(成交 ts)` | 同普通轮次 |
| `--data-dir DIR` | 把整个 `data/` 重定向到 `DIR`（state/logs/raw/reports/exports 都跟随） | 创建 `DIR` 下子目录 | — |
| `--status` | 只读影子产物：快照是否存在、`generated_at`、候选累计/当日 P0-P1、最近 3 条候选、**快照里的归因摘要**与 exports 路径 | 不联网、不跑采集、不建目录、不读 exports 文件 | 中文摘要或（配合 `--json`）状态对象 |

没有 `--once`、`--test`、`--report` 等其它开关。`--fixtures` 与 `--data-dir` 通常一起用于测试隔离；`--data-dir` **不会**改变 `crypto-price` exports 的读取路径（该路径相对模块根解析，fixtures 模式下改用 `price_export.json`）。

## 6. 退出码

| 退出码 | 触发条件 | 说明 |
|---|---|---|
| `0` | 正常完成；或 `--status` 且快照存在 | 可能仍有非致命降级，但 `errors` 为空 |
| `1` | 有产出但 `errors` 非空（单源失败/`price_context` 不可用）；或 `--status` 且快照不存在 | 降级轮次仍会落盘 |
| `2` | 配置 `schema_version` 不符（或 argparse 参数用法错误，由标准库返回） | 不进入采集，不写产物 |
| `3` | 主要数据源全部不可用：无任何资产有逐笔成交、`n_eligible=0`、稳定币 `n=0` | 快照仍会写（结构完整），`source_health` 全空 |

判定顺序（`run_once` 尾部）：先看"三块主数据是否全空"→3；否则看 `errors`→1；否则 0。walletflow 的 RPC 失败只写快照 `walletflow.error`、walletflow 告警失败只写 `walletflow.alerts.error`/`pending`，**不进入 `errors[]`、不影响退出码**。`--dry-run` 与普通轮次的退出码规则相同，不因不写盘而改变。注意：若快照/候选/exports 写盘抛出 `OSError`，进程异常退出（rc≠0），由 `collect.sh` 的 start/done 日志与 healthcheck 记录。

## 7. 状态与产物

### 7.1 `data/state/shadow-latest.json`

每轮覆盖写（原子写）。顶层键：

| 键 | 内容 |
|---|---|
| `schema` / `source_module` / `mode` | 固定 `onchain/shadow/v1` / `crypto-onchain` / `shadow` |
| `generated_at` / `now_ts` | 本地时区 ISO 时间 / epoch 秒 |
| `assets` | `{BTC/ETH/BNB: {price, change_24h_pct, direction, oi, funding_rate, oi_change_15m_pct, taker_5m, taker_15m, liq_15m, sources}}` |
| `cex_flows` | `{exchanges[], n, n_eligible, tvl_usd, net_24h_usd, net_24h_bps, net_1w_usd, net_1w_bps, net_1m_usd}` |
| `stablecoins` | `{n, circulating_usd, net_issue_24h_usd, net_issue_1w_usd, symbols[]}` |
| `price_context` | `{ok, fresh, generated_at, assets{}, error}`（只读 `crypto-price` 的结果与降级原因；assets 内含 `funding_rate`/`oi`） |
| `candidates` | 本轮候选数组（结构与 signals 层一致，命中归因的候选带 `attribution` 摘要） |
| `attribution` | **M2 新增** `{assets: {asset: 归因对象或 null}, market: 归因对象或 null}` |
| `walletflow` | **M3 附加**：链上大额转账摘要 `{enabled, source, chains[], n_large, usd_total, recent[≤5], alerts, errors[]}`；`chains[]` 每项含 `name/latest_block/from_block/to_block/scanned_blocks/skipped_blocks/rpc_calls/n_large/usd_total/error`；`alerts={sent_round, suppressed, error}`（告警开启且非 fixtures 时存在，告警异常时 `error` 非空），dry-run 时另有内存字段 `alerts_preview`（不落盘）；关闭时为 `{enabled: false}`，失败链的 `error` 在对应 `chains[]` 项 |
| `source_health` | `{ok, total, cex_ok, stablecoin_ok, errors[]}` |
| `errors` | 本轮降级项字符串数组 |

`taker_5m`/`taker_15m` 字段：`window_seconds, n_trades, quote_volume_usd, sell_quote_usd, buy_quote_usd, sell_ratio, large_sell_count, large_sell_usd, large_buy_count, price_first, price_last, window_change_pct, coverage_seconds, first_ts, last_ts`。
`liq_15m` 字段：`window_minutes, n, n_total, total_usd, long_usd, short_usd, long_ratio, coverage_minutes, usd_known, first_ts, last_ts`。

### 7.2 `data/state/oi-history.json`

跨轮 OI 历史（M2 新增，唯一的跨轮状态）：

- 结构：`{asset: [[ts, oi], ...]}`；`ts` 为 epoch 秒，`oi` 来自 `crypto-price` exports（Binance USD-M `openInterest`）；
- 写入：每轮非 dry-run 覆盖写；只保留 24h（`attribution.oi_history_hours`）内的点，每资产最多 400 点；
- 读取：`_load_oi_history` 用 `common.safe_json_load`，坏数据/非对象一律按空；
- 用途：算 `oi_change_15m_pct`（15m 窗口、10 分钟容差），供归因升降置信度；
- 写入失败（`OSError`）静默跳过，不影响本轮归因。

### 7.3 `data/state/shadow-candidates.jsonl`

每条候选一行、只追加；字段 = 候选结构（`signal/scope/asset/exchange/severity/value/threshold/direction/aligned/would_alert/note/extra`）+ `generated_at/now_ts/price_context/cex_net_24h_bps/attribution`。无候选的轮次不写行。

### 7.4 `data/exports/onchain-pressure.json`

跨模块契约 `onchain/pressure/v1`（字段表见 [design.md](design.md) §8）：每轮非 dry-run 覆盖写，路径由 `app.exports.file` 决定（默认 `exports/onchain-pressure.json`）。内容含 `assets`（每项含 `oi_change_15m_pct`、`funding_rate` 与完整 `attribution`）、`market`（资金流 + 市场级归因）、`candidates` 计数、**顶层 `walletflow`（M3 附加，与快照同源，只增字段）**与固定 `caveats`。**目前没有下游消费方**（crypto-analyst 规划中）。

### 7.5 日志与状态查询

- `data/logs/onchain.log`：每轮一行 `shadow: assets=N cex_n=N stable_n=N candidates=N attributions=N walletflow=N errors=N`；由 `collect.sh` 每日首轮调用 `tools/prune.py` 清理。
- `collect.sh` 的 `data/logs/collect.log`：每轮 start/done（含 rc），看门狗据此判定最近一轮；单实例锁目录 `data/state/collect.lock`。
- `--status` 返回：`snapshot, snapshot_ok, generated_at, candidates_file, candidates_total, candidates_today, candidates_today_severity{P0,P1}, last_candidates, attribution, walletflow, exports`；当日按 `generated_at` 的日期前缀统计；中文摘要追加"归因: …"一行（`walletflow`（含 `alerts` 摘要）只在 `--json` 输出里）。
- M2 **没有** `delivery.json`、`feedback.jsonl`、`calibration.jsonl`（M3）；healthcheck 状态由仓库级 `tools/healthcheck.py` 写入本模块 `data/state/health.json`（`collect.sh` 已接入，异常才告警/自愈）。

### 7.6 链上大额转账状态与明细（M3 附加）

- `data/state/walletflow-cursor.json`：`{chains: {ethereum: block, bsc: block}, updated_at}`；每条链取本轮 `to_block`（无则 `latest_block`），**失败链不前进**。非 `--dry-run` 时覆盖写；积压超过单轮上限时只扫最近 N 块并记 `skipped_blocks`。
- `data/raw/<date>/walletflow.jsonl`：当日大额记录，只追加；字段 `ts/block/symbol/amount/usd/from/to/tx/log_index/direction/counterparty`（不含 `chain`/`token`/`kind`）。`--dry-run` 不写；无记录不写空行。
- 快照 `shadow-latest.json` 的 `walletflow` 块与 exports 顶层 `walletflow` 字段同源（字段表见 [design.md](design.md) §9.6）；`--status` 的 JSON 输出包含快照里的 `walletflow` 摘要（中文摘要不单独打印）。

### 7.7 链上大额转账告警状态（事件驱动告警）

`data/state/walletflow-alerts.json`（`walletflow_alerts.STATE_FILE = "state/walletflow-alerts.json"`，随 `--data-dir` 重定向）：

```json
{
  "date": "2026-11-09",
  "messages": 3,
  "sent": [{"key": "0xabc:0", "severity": "P0", "usd": 60000000, "ts": "2026-11-09T12:00:00"}],
  "suppressed": [{"key": "0xdef:1", "severity": "P1", "usd": 12000000, "reason": "daily_cap"}],
  "pending": [],
  "last_sent_at": "2026-11-09T12:00:00"
}
```

- 键 `(tx, log_index)`（`_key`）；`messages` 是当日**发送成功**的消息条数（一条含多笔只算 1）；`sent` 只记发送成功的记录；`suppressed` 记 `reason=quiet_hours/daily_cap/round_limit`（`quiet_hours` 仅在显式配置发送时段时出现）；`pending` 为发送失败待重试（≤20）；特殊期只解除 `daily_cap`/`burst_cap`，不改变去重键与状态结构。
- 跨日（`date` 变化）整体重置；`--dry-run` **不写**该文件、不发送；`--fixtures` 不调用告警。
- 日期格式：`date` 为 `YYYY-MM-DD`（本地时区），时间为 `YYYY-MM-DDTHH:MM:SS`。
- 读取方：`run.py` 汇总 `snapshot.walletflow.alerts = {sent_round, suppressed, error}`；`digest.py` 读 `messages`/`suppressed`/`alerts.daily_cap` 生成“今日链上告警”行。

### 7.8 地址标签缓存（告警补标签）

`data/state/address-tags.json`（`TAG_CACHE = "state/address-tags.json"`，随 `--data-dir` 重定向）：

```json
{
  "0x1111111111111111111111111111111111111111": {"tag": "Binance 14", "ts": 1794176000}
}
```

- 仅 `walletflow_alerts.enrich_tags` 读写；`--dry-run` 不调用 `enrich_tags`，因此不读不写、不联网。
- 键为小写地址；`tag` 为 Etherscan 地址页 name tag（空串表示抓过但没抓到）；`ts` 为抓取时刻 epoch 秒。
- TTL = `alerts.enrich.cache_days`（默认 30 天）：命中且未过期直接用（空标签也不重抓）；过期/未命中才抓；**失败也写缓存**避免反复抓。
- 读坏文件按空对象处理；写盘 `OSError` 静默吞掉；缓存不参与数据记录/exports。

### 7.9 每日摘要调用链

`collect.sh` 在跑完一轮、healthcheck、prune 之后调用 `python3 scripts/digest.py --if-due --notify`：

1. 读 `app.digest`，未到 `time`/今天已发 → 打印跳过并退出 0；
2. 读 `state/shadow-latest.json` + 当日候选 jsonl + `raw/<date>/attributions.jsonl` + `raw/<date>/walletflow.jsonl` + `state/walletflow-alerts.json`；
3. 渲染摘要文本（含“今日链上告警”行、校准 Day N/14、固定口径说明）；
4. `--dry-run` 打印；否则从根 `.env` 取 Telegram 凭据发送，成功后写 `state/digest.json` 并记 `onchain.log`；
5. 调用处带 `|| true`：摘要失败不影响采集轮次 rc。

## 8. 网络与运行预算

| 项 | 每轮请求 | 权重/限流 | 超时/重试 |
|---|---|---|---|
| Binance 现货 `aggTrades` | 每资产 ≤12 页（回补 900 秒） | 权重 4/页；3 资产合计 ≤144 权重/轮 | 8 秒；0 重试 |
| OKX 清算 | 每资产 1 次（`limit=100`） | 公开接口 | 8 秒；1 次重试 |
| DefiLlama `/cexs` | 1 次 | 公开接口 | 12 秒；1 次重试 |
| DefiLlama stablecoins | 1 次 | 公开接口 | 12 秒；1 次重试 |
| OI 历史 / exports | 0 次网络请求 | 本地文件读写 | — |
| walletflow 公共 RPC | 每链 1 次 `eth_blockNumber` + ETH 2 币种×4 次（120÷30）+ BSC 2 币种×3–6 次（900÷300；节点报“范围过大”时自动对半拆分）+ 批量时间戳 1 次 | 免 key；实测约 20–40 次/轮、30–60 秒 | 25 秒；0 重试（失败链下轮重试） |
| Etherscan 地址页（仅告警补标签） | 仅在本轮**真实发送**告警且地址未命中缓存时请求，去重后通常 0–2 个/轮（上限为单轮 5 笔×双边地址） | 免 key；页面抓取、非官方 API；best-effort | 6 秒（`alerts.enrich.timeout_seconds`）；0 重试；失败静默；`--dry-run` 0 次 |
| Telegram 告警（walletflow） | 仅在本轮有告警时 1 次 `sendMessage`/轮 | Telegram Bot API；免费 | 由 `notify` 内置超时控制；失败留 `pending` 下轮重试 |
| Telegram 每日摘要 | 每天最多 1 次（21:00 到点且当日未发） | Telegram Bot API；免费 | 失败不写 `digest.json`，下一轮重试 |

按 `app.yaml` 的 `poll_seconds: 300` 折算，Binance 峰值约 3×12×4÷5 ≈ **28.8 权重/分钟**，远低于 6000/min 预算；实际轮询 300 秒一轮（launchd `StartInterval` 由模板渲染）。数据源仍全部免 key、$0；**钱包告警与每日摘要优先用 `.env` 的 `TELEGRAM_ONCHAIN_BOT_TOKEN`/`TELEGRAM_ONCHAIN_CHAT_ID`（独立 Bot，未填回退通用 `TELEGRAM_*`）**（缺失只记 error/留 pending，不阻塞采集）；`ETHERSCAN_API_KEY` 未被读取。单轮耗时会打印在摘要/JSON 的 `elapsed_seconds`。

## 9. 测试与 `verify.sh`

150 项离线单测，全部不联网、不读凭据（告警测试用临时目录 + 假凭据路径，不发送；标签测试用临时缓存目录，不发请求）：

| 文件 | 项数 | 重点断言 |
|---|---|---|
| `tests/test_indicators.py` | 18 | 占比/大单/窗口/空样本/坏行；方向阈值；**`oi_change_pct` 变化值、历史不足、容差超限、点数不足、字典/坏行**；CEX bps + `min_tvl_usd`；稳定币求和；清算 USD 与多空比、窗口、价格缺失 |
| `tests/test_signals.py` | 22 | P1/P0 边界、样本门槛、按资产分层、`require_price_direction` 开关、`aligned`/`would_alert`、note 身份词黑名单、1w 慢变量、清算覆盖/单条样本/类别、稳定币 P0 需 BTC 跌幅、排序 |
| `tests/test_attribution.py` | 15 | 4 类归因与置信度升降、价格门控、`enabled=false`、taker 小样本忽略、市场级门控、`build_attributions` 形状、三段字段与身份词黑名单 |
| `tests/test_digest.py` | 7 | 到点/已发/未启用判断；摘要统计（候选数/归因类别/校准 Day）；dry-run 不写状态；JSON 输出 |
| `tests/test_walletflow.py` | 12 | Transfer 解码（大额/低于阈值/mint-burn 默认排除/坏日志）；阈值过滤 + `(tx, log_index)` 去重；可选标签方向；扫描区间（首轮回看/增量/缺口封顶/无新块）；`summarize` 与多链 fixture 回放 |
| `tests/test_walletflow_alerts.py` | 21 | 分级（单笔 ≥$50M、入所 ≥$25M）；日限额扩容公式（10/20/30 边界）；发送时段（默认全天即时；显式配置时 P1 抑制、P0 穿透）；**特殊期判定**（大额笔数/\|BTC 24h\| / CEX 净流出/稳定币净赎回四条 + `manual` 手工强制；特殊期 `cap`/`hard` 为 `None`）；去重（`tx:log_index`；同 tx 多腿只留最大一腿）；推测备注（关联 ≥3 / 频次 ≥5 / 无历史 / 已标签不猜）；聚合“（N 笔）”；预览不写状态；`build_history`；**地址标签补全**（`parse_etherscan_tag` 解析页面 name tag / 空页面返回空串；`enrich_tags` 命中缓存直接返回、**不联网**） |
| `tests/test_run_fixtures.py` | 5 | fixtures 全链路快照/候选/jsonl；**归因类别/置信度/候选归因摘要/exports 字段**；**`walletflow` 快照与 exports 字段（ETH $10M / BSC $5M：8M 过滤、12M USDC + 7M BSC 保留）**；`--dry-run` 不落任何文件（含 OI 历史与 exports）；全流程身份词黑名单；全源缺失 rc=3；`--status` |

运行方式：

```bash
cd crypto-onchain
../.venv/bin/python -m unittest discover -s tests -v   # 只跑模块 150 项
./scripts/verify.sh                                    # ruff + libs 单测 + 模块单测 + fixtures e2e
./scripts/verify.sh --with-network                     # 追加一次真实采集 --dry-run（临时目录）
```

`verify.sh` 的分步输出为 `1/4 ruff` → `2/4 libs` → `3/4 本模块` → `4/4 fixtures e2e`；ruff/venv 缺失时分别跳过或回退 `python3`；`--with-network` 时追加 `5/5`。fixtures 覆盖：BTC 主动卖压 + 多头强平（OI 历史种点 → `high`）、ETH 清算、BNB 资金撤离、CEX 净流出、稳定币净赎回、**walletflow 回放（ETH 8M USDT 低于 $10M 被过滤、重复项去重、12M USDC 与 BSC 7M USDT 入快照/exports；BSC 9M mint 默认排除）**，以及 BNB 低于清算阈值不触发、ETH 主动卖占比不足不触发等反例。walletflow 告警用独立单测覆盖，**e2e 不发送、不写告警状态**。

## 10. 依赖清单（`libs/crypto_core`）

| 能力 | 使用的函数/模块 | 用途 |
|---|---|---|
| HTTP | `crypto_core.http.get` | Binance/OKX/DefiLlama 取数；异常统一降级 |
| 存储 | `store.safe_json_load` / `safe_json_dump` / `append_jsonl` / `read_jsonl` | 原子写快照/导出/OI 历史、追加候选、读状态 |
| 配置 | `config.load_yaml` / `check_schema_version` | 三份 YAML 与 `schema_version` 校验 |
| 时间 | `timeutil.now` / `today_str` / `ts_iso` | 时间戳、当日统计、ISO 时间 |
| 环境 | `env.load_env` | **已调用**：`walletflow_alerts.py` / `digest.py` 读取根 `.env` 的 Telegram 凭据 |
| 通知 | `notify.check_credentials` / `send_message` / `esc` / `job_label` | walletflow 事件告警与每日摘要的 Telegram 发送（HTML、免预览） |
| Binance 解析 | `market.binance.parse_agg_trades` | `aggTrades` → `id,price,qty,quote_qty,ts,taker_side`（ms→s、`m=true`→sell） |
| DefiLlama 解析 | `market.defillama.parse_cex_flows` | `/cexs` → `name,slug,tvl,inflows_24h/1w/1m` |
| DefiLlama 解析 | `market.defillama.parse_stablecoin_supply` | stablecoins → `circulating,prev_day,prev_week,net_issue_24h/1w` |
| OKX 解析 | `market.okx.parse_liquidations` | 清算 `details[]` 扁平化 → `inst_id,uly,pos_side,side,price,size,ts` |

M1 在 `libs` 新增的 5 个公开解析器中，本模块**实际调用 4 个**（`parse_agg_trades` / `parse_cex_flows` / `parse_stablecoin_supply` / `parse_liquidations`）；`okx.parse_instruments`（拉取 `ctVal`）未被调用——合约面值采用 `watchlist.yaml` 的静态 `contract_value`。M2 归因层只依赖标准库 `math` 与模块内指标；walletflow 用标准库 `urllib` 直连公共 RPC（不经过 libs `http`）；**walletflow_alerts.py 与 digest.py 使用 libs 的 `notify`（Telegram）**，这是 M2 之后唯一新增的 libs 能力复用（`events`、`dedupe` 仍未使用）。告警补标签同样只用标准库 `urllib` + `re`（**不新增依赖、不新增 libs 模块**）。

## 11. 当前限制与 M3 接入点

| 限制（M2 实测行为） | 依据 | M3 计划 |
|---|---|---|
| 4 类卖压候选无 Telegram 推送、无卡片渲染 | 候选不走 `notify`；`delivery.*` 不被读取 | 按 `delivery.mode` 接 `alert/v1`，渲染证据/反证/不可知三段 |
| 4 类卖压候选无去重冷却 / 静默 / 日限额 | `delivery.*` 只是配置骨架 | M3 在送达层实现，并补单测 |
| 无 feedback / calibration 回算 | 无 `feedback.jsonl`/`calibration.jsonl` 代码 | 用 `shadow-candidates.jsonl` 离线回算，再决定是否加闭环 |
| exports 无下游消费 | 只写 `onchain-pressure.json` | `crypto-analyst` 接入 `onchain/pressure/v1` |
| 归因不做数据质量门 | 过期 `price_context` 的方向被置 unknown，但 `change_24h_pct` 仍可能触发价格门控 | 加 `data_quality` P2 + exports 过期拦截 |
| run.py 自身无单实例锁 | 锁在 `collect.sh`（`mkdir data/state/collect.lock`） | M3 视需要下沉到 run.py（对齐 crypto-price 退出码 4） |
| 无 taker 跨轮状态 | 只保存 OI 历史；"连续 3 窗口"、P0 5m 跌幅未实现 | 视影子校准结果再决定是否加 |
| launchd 已安装（影子 + 链上告警） | `com.crypto-research.crypto-onchain`，300s，采集 + 归因 + walletflow 事件告警；4 类卖压候选不推送（2026-11-09） | 4 类候选上线推送时改 `poll_seconds` 并重装 |
| 链上大额转账仅 ETH 主网 + BSC 的 USDT/USDC | `walletflow.chains[].tokens` 仅两项；公共 RPC 单轮上限按链 | 视需要加更多稳定币/链（Etherscan key 未被使用，保留为备用，不默认接入） |
| walletflow 数据记录不判地址归属/实体 | 内置 23 个已验证标签（仅 ETH 主网），仅标方向；告警的“推测”备注明确标注、不写归属字段 | 不接付费标签；`labels` 定期按官方清单+Etherscan 核对更新；**BSC 无免费标签源** |
| walletflow 告警会“猜”但只作线索 | 方向 `unknown` 时按 7 天历史给“疑似与 X 关联/疑似高频机构地址”，固定带“推测/疑似” | 保留（设计要求）；不做实体断言、不写入数据字段 |
| 告警地址标签来自内置清单或 Etherscan 地址页抓取 | `enrich_tags` 抓 `etherscan.io/address/<addr>` 页面 HTML，仅 ETH，缓存 `state/address-tags.json` 30 天；BSC 无标签源 | 保留（页面结构变化时静默降级；不接付费标签、不做实体断言） |
| walletflow 告警已有限额/全天即时/特殊期 | `only_p0=true`（P1 不推送，reason=only_p0）；常规 ≤10 条/天（特殊期解除）；`quiet_hours` 默认空 = 全天即时（可配置）；特殊期解除 `daily_cap`/`burst_cap`（保留去重与单轮 5 笔）；`(tx, log_index)` 去重 | 保留；4 类卖压候选的去重/冷却仍属 M3 |
| walletflow RPC/告警失败不阻塞、不改 rc | 只写快照 `walletflow.error`/`walletflow.alerts.error`，游标不前进、告警留 `pending` | 保留（失败下轮自动重试） |
| 无自动回算脚本 | 影子期精确率/日均候选需离线分析 jsonl | M3 补脚本 |
| 日志清理已接入 | `collect.sh` 每日首轮 `tools/prune.py --apply` | — |
| healthcheck 已接入 | `collect.sh` 每轮 `tools/healthcheck.py --quiet --notify --fix` | — |
| `--status`/`--dry-run` 严格只读 | `data_paths` 只算路径，不建目录；dry-run 不写 OI 历史/exports/告警状态 | 保留 |
| fixtures 时效不校验；fixtures 不发告警 | `_price_context_offline` 固定 `fresh=true`；`run.py` 在 `fixtures_dir` 下跳过 walletflow_alerts | 保留（回放场景） |
