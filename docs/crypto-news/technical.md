# 技术架构：crypto-news（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求见 [requirement.md](requirement.md)；实现细节见 [design.md](design.md)。
> **仓库级**技术架构（分层/依赖/调度）见 [../architecture.md](../architecture.md)。

## 1. 模块结构

```
crypto-news/
├── scripts/
│   ├── common.py        # 薄适配层：把 libs 加入 sys.path + 路径常量（不实现业务）
│   ├── fetch.py         # 采集/归一化：44 源 → data/raw/<date>/items.jsonl；源游标 data/state/<source>.json
│   ├── score.py         # 过滤/聚类/分类/评分/分级；产出 alerts-<date>.json + reports/<date>.md
│   ├── notify.py        # CLI：读 alerts → 去重/静默/格式化 → Telegram
│   ├── collect.sh       # 定时入口：锁 → run.sh --notify → healthcheck → 每日 prune
│   ├── run.sh           # 手工入口：fetch → score（+ --notify 推送）
│   ├── verify.sh        # 模块质量门（ruff + libs/模块单测 + 端到端重评分）
│   ├── inspect.sh       # 被 healthcheck 调用的 AI 巡检入口（dsh headless）
│   └── install_launchd.sh  # 薄封装 → tools/render_launchd.py
├── config/*.yaml        # watchlist/sources/people/entities/rules/thresholds/notify
├── tests/               # 52 项离线单测（配置/采集解析/聚类/评分/分级/卡片/去重）
├── deploy/launchd/      # agent / healthcheck / inspect 三个 plist 模板
└── data/                # raw/ state/ reports/ exports/ logs/（gitignored）
```

## 2. 组件职责与数据契约

| 阶段 | 文件 | 输入 | 输出 |
|---|---|---|---|
| 采集 | `fetch.py` | `config/sources.yaml` + `data/state/<source>.json` | `data/raw/<date>/items.jsonl`（追加去重） |
| 评分 | `score.py` | 当日 raw + config | `data/state/alerts-<date>.json`、`data/reports/<date>.md` |
| 推送 | `notify.py` | alerts + `data/state/notified.json` | Telegram + 去重状态 |
| 运维 | `collect.sh` / `tools/healthcheck.py` | 日志 / data 体积 / launchd 状态 | 自愈动作 / 告警 / 修复报告 |

## 3. 运行链路

```
launchd(600s) → collect.sh（锁）
  → run.sh → fetch.py → score.py → notify.py --send
  → healthcheck --quiet --notify --fix --agent（正常静默；异常自愈/告警；首次立即调 AI，之后多级退避）
  → 当天首轮：tools/prune.py --apply
```

## 4. 运行与检查周期

| 项目 | 周期 / 阈值 |
|---|---|
| 运行周期 | launchd **每 600 秒**（`StartInterval=600`）；单轮 20–40 秒；单实例锁，上一轮未结束则跳过 |
| 数据源采集 | 每轮采集全部 44 个 `verified` 源（失败单源不断轮） |
| 自检周期 | **每轮结束**执行 `healthcheck --quiet`（正常零输出）；「最近一轮未运行」阈值 **25 分钟**；同一告警 **60 分钟**内不重复 |
| AI 深度分析 | 仅异常（FAIL）时触发，**首次立即**；未恢复按 **15→30→60→120→240→360 分钟**多级退避（`agent_attempts` 存 `health.json`，恢复即清零）；**后台异步**执行（`tools/agent_repair.py`，主任务不等它），单次超时 **600 秒**；结束后由它统一发「修复报告」 |
| 夜间策略 | **全天即时（无静默）**；P0/P1 都发 |
| 事件去重 / 衰减 | 同事件 **24h** 内只推一次；衰减窗口 security/regulatory/exchange **h=24h**、market/macro **h=6h**；硬条件窗口 **48h**（48–96h 降 P1，>96h 只归档） |
| 数据清理 | **每天首轮**执行 prune：raw **90 天**、logs **30 天**（单文件 >2MB 裁剪到最后 2000 行）、reports **365 天**、alerts **180 天**、exports **7 天**；`data/` 总量 **500MB 硬上限** |
| 手工触发 | `launchctl kickstart -k gui/$(id -u)/com.crypto-research.crypto-news` |


## 4.1 英文标题翻译（AI）

- `tools/translate_titles.py`：读 JSON 标题数组 → DeepSeek API → 等长中文数组；
- `notify.py` 发送前批量翻译并写 `data/state/translate-cache.json`，失败回退 `libs/text.cn_summary` 规则版。

## 5. 依赖（libs/crypto_core）

`paths/env/config/store/timeutil/text` · `http` · `sources.rss/announcements/telegram` · `market.*`（行情参考）· `events` · `dedupe` · `notify`。

## 6. 状态与数据

- `state/`：源游标（每源有界 3000→1000）、`seen.json`（每日重置）、`notified.json`（按天）、`alerts-<date>.json`、`health.json`。
- `raw` 可回放：评分规则调整后可对历史 raw 重跑。
- 保留策略见 [design.md](design.md) §5 与 [Runbook §7](../runbook.md)。

## 7. 测试

- 52 项离线单测（无网络）；`make verify` 会跑 ruff + libs + 模块单测 + 端到端重评分。
- 解析层用内联 fixtures，CI 不联网。
- 联网冒烟仅本地可选：`./scripts/verify.sh --with-network`。

## 8. 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
| 单源失败 | 该源记 error，其余继续 | 继续本轮，日志记录；healthcheck 采集成功率告警 |
| 全源失败 | rc=3 | 下一轮重试；healthcheck 告警 |
| 上一轮被强杀 | 锁残留 | healthcheck 30 分钟后清锁 |
| 凭据错误 | rc=2 | healthcheck 告警 + 提示 `.env` |
| AI 超时 | rc=timeout | 中止并照常发「修复报告」（仍异常/需人工） |
