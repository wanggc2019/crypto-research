# crypto-jobs · 加密行业技术岗位（DevOps/SRE/MLOps/开发）市场情报

> 本目录是 `crypto-research` 仓库的需求模块；仓库约定见 [../README.md](../README.md) 与
> [../docs/conventions.md](../docs/conventions.md)。共享能力用 `libs/crypto_core`。

**做什么**：只盯加密行业的技术岗位（DevOps / SRE / MLOps / 平台基础设施 + 开发岗位），每天从公开 ATS/招聘站
全量采集 → 规则词典抽取技能 → 存每日快照 → 和周 baseline 对比 → 每周出技能趋势周报并推 Telegram。

**规模（2026-11-09 实测，含开发岗位）**：40 个公开源（T0 35 / T1 2 / T2 3）→ 1931 条原始岗位
→ **457 条目标岗位**（57 家公司，含 CEX/DEX；DevOps/SRE/MLOps/平台 + 后端/前端/全栈/移动/区块链/数据/ML/嵌入式/交易系统）；
技能词典 225 个技能 / 20 类；单轮约 35–90 秒、零成本。

**不做什么**：不抓 LinkedIn/Indeed/Glassdoor；不做简历投递；不宣称全市场普查；不用 LLM 逐条读 JD。

## 运行

```bash
./scripts/run.sh --dry-run --json   # 联网抓一轮，只打印（不写盘、不推送）
./scripts/run.sh                    # 日更：raw + 快照 + 索引 + 日报 + exports
./scripts/run.sh --notify --daily-digest  # 手动补发日报摘要（忽略开关；同日去重）
./scripts/run.sh --weekly           # 周报（只读历史快照）
./scripts/run.sh --weekly --notify  # 生成并推送周报（摘要 + 完整 Markdown 文件；同日重复默认跳过）
./scripts/run.sh --alerts --notify  # 立即扫描一次新岗位并推送速报（无新增则静默）
./scripts/run.sh --weekly --notify --force  # 同日强制重发
./scripts/run.sh --test --dry-run   # Telegram 通道 dry-run
python3 scripts/e2e.py              # 离线全链路回放（CI/gate 用）
```

调试：

```bash
./scripts/run.sh --dry-run --limit 5 --only greenhouse:coinbase --json   # 只跑一个源
./scripts/run.sh --fixtures tests/fixtures/sources --dry-run             # 离线回放
```

调度（launchd，`tools/render_launchd.py` 渲染）：

```bash
make launchd module=crypto-jobs action=install                  # 报告任务：每天 18:00 日更 + 周日 10:00 周报
make launchd module=crypto-jobs action=install template=alert   # 事件速报：每 2 小时扫描新岗位
make launchd module=crypto-jobs action=status
```

## 产物（`data/`，gitignored）

| 产物 | 路径 |
|---|---|
| 原始岗位（可回放） | `data/raw/<date>/jobs.jsonl`、`source-health.json` |
| 每日快照 / 索引 | `data/state/snapshots/<date>.json`、`data/state/jobs-index.json` |
| 日报 / 周报 | `data/reports/<date>.md`、`data/reports/<date>-weekly.md`（周报含全量公司招聘统计 + Top10 画像） |
| 跨模块导出 | `data/exports/skill-report.json`、`jobs-latest.json`、`company-stats.json`（每公司招聘/角色/技能统计） |
| 运行状态 | `data/state/last_run.json`、`data/logs/crypto-jobs.log` |

## 配置（业务策略都在 `config/`）

| 文件 | 作用 |
|---|---|
| `app.yaml` | 采集/过滤/报告阈值/送达/快照保留 |
| `roles.yaml` | 目标角色（DevOps/SRE/MLOps/平台/开发）、排除词、资历规则 |
| `skills.yaml` | **技能分类法**：新增技能/别名/正则改这里；`drilldown` 把 AI/可观测性/钱包等大项拆成具体技能（配单测） |
| `sources.yaml` | 源注册表：id/类型/tier/url/parser/ToS 备注；失效源改这里 |
| `exchanges.yaml` | CEX / DEX 名单（用于周报的交易所动态与 CEX vs DEX 对比） |

## 需求 / 设计 / 架构

- [需求](../docs/crypto-jobs/requirement.md)
- [设计（选源/合规/技能抽取/指标/监测闭环）](../docs/crypto-jobs/design.md)
- [业务架构图](../docs/crypto-jobs/architecture.md)
- [技术架构](../docs/crypto-jobs/technical.md)

## 送达 / 凭据

- 默认用仓库根 `.env` 的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（与其他模块共用 Bot）。
- 想让本模块报告单独用另一个 Bot（如 **独立 Bot**）：在 `.env` 加
  `TELEGRAM_JOBS_BOT_TOKEN`（可选 `TELEGRAM_JOBS_CHAT_ID`，不填回退通用 chat）；
  也兼容 `TELEGRAM_JOBS_BOT_TOKEN` 这类自定义命名。
- 确认当前用的是哪个 Bot：`python3 scripts/notify.py --whoami`；发测试卡：`./scripts/run.sh --test`。

## 约定

- 采集层只负责抓取 + 字段归一；岗位角色/技能/指标逻辑在 `normalize.py` / `skills.py` / `metrics.py`，
  纯函数优先、离线可测。
- 所有对外副作用（网络、推送、写盘）都能 `--dry-run` 或 `--fixtures` 关掉。
- 单源失败不拖垮整轮；`source_health` 必须如实记录。
- 运行产物写 `data/`；凭据只读仓库根 `.env`；模块间零 import；`make gate` 必须离线全绿。
