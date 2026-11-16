# 交接文档（SESSION HANDOFF）

> 用途：新会话读完 [AGENTS.md](../AGENTS.md) 后读这份，只放**现在成立的事实 + 待办**。
> 模块细节（规则、阈值、实测数字、命令全集）在各模块 README / `docs/<module>/`，这里不复制；
> 决策的来龙去脉在 [ADR](adr/)；历史在 `git log`。
> 最近更新：2026-11-16

## 0. 项目一句话

个人加密情报监测系统：把分散、嘈杂的加密信息压缩成每天少量可决策信号，Telegram 送达。
一个仓库 = 若干自包含 `crypto-*` 模块 + 共享库 `libs/crypto_core`（结构见 [architecture.md](architecture.md)）。

## 1. 设计约束（默认配置，不要擅自改）

| 项 | 结论 |
|---|---|
| 关注范围 | **BTC / ETH / BNB**；SOL、ASTER 已冻结；其他山寨不看 |
| 社交监控 | 不监控泛 KOL 喊单；crypto-news 只盯头部交易所创始人/CEO 本人原创（`crypto-news/config/people.yaml`）；crypto-x-kol 只看白名单 |
| 预算 | 零成本：不买 VPS、X API、CoinGlass |
| 送达 | Telegram（凭据只在根 `.env`）；**全天即时、无夜间静默**；总结类按 [notify-policy](notify-policy.md) 的空闲窗口 |
| 语言 | 中文为主，保留英文源原文标题 |
| 工作方式 | 见 [AGENTS.md](../AGENTS.md)：分支 → `make gate` → PR → CI 绿 → squash merge；不直推 main |
| 仓库结构 | 模块间零 import；跨模块只读对方 `data/exports/`（契约登记见 ADR 0008）；共享逻辑进 `libs/crypto_core` |

## 2. 模块与定时任务（2026-11-16，`make jobs` 为准）

| 模块 | 职责 | launchd 频率 | 详情 |
|---|---|---|---|
| crypto-price | BTC/ETH/BNB 价格/衍生品异动告警，三源核验 | 每 1 分钟 | [README](../crypto-price/README.md) |
| crypto-news | 消息面采集/评分/告警 | 每 10 分钟 | [README](../crypto-news/README.md) |
| crypto-x-kol | 白名单 KOL 的 X 推文监控（r.jina.ai） | 每 5 分钟 | [README](../crypto-x-kol/README.md) |
| crypto-onchain | 资金流卖压归因 + 链上大额转账告警 | 每 5 分钟 | [README](../crypto-onchain/README.md) |
| crypto-policy | 国内外监管政策雷达 + AI 解读 | 每 1 小时 | [README](../crypto-policy/README.md) |
| crypto-analyst | 日报 / 周报 / 月度归档（只读 exports） | 每日 18:30、周日 22:00、每月 1 日 22:00 | [README](../crypto-analyst/README.md) |
| crypto-jobs | 加密行业技术岗位情报 | 每日 18:00、周日 10:00；`.alert` 每 2 小时 | [README](../crypto-jobs/README.md) |
| crypto-knowledge | 每日学习课程（SRS 复习） | 每日 13/19/21/23 点 | [README](../crypto-knowledge/README.md) |
| 仓库级 watchdog | 全仓健康检查（唯一负责方） | 每 5 分钟 | [runbook](runbook.md) |

部分模块用独立 Bot（`.env` 的 `TELEGRAM_<MODULE>_*`，未填回退共享 Bot），见各模块 README。

## 3. 当前状态（仓库级能力）

- **质量门**：`make gate` 是唯一入口（本地 / pre-push / CI 同一套）：ruff + 全部单测 + 各模块 `scripts/e2e.py` 离线回放 + 契约/公开信息/重复审计 + launchd 检查；`make verify` 是兼容别名。
- **一轮骨架**：各模块 `scripts/collect.sh` 都 source `tools/lib/module_round.sh`（锁、日志格式、每日 prune、`after()` 钩子）。
- **健康检查**：由 watchdog 统一负责；其心跳 `data/state/watchdog.json` 超过 15 分钟未更新时，各模块每轮兜底自查（见 `tools/lib/module_round.sh` 头注释）。
- **抓取限速**：`crypto_core.http` 对 `r.jina.ai` 做跨进程共享限速（默认 18/min，`JINA_RPM` 可调）。
- **跨模块契约**：`crypto_core.contracts` 登记 6 个 exports 契约，消费方统一 `contracts.read()`（ADR 0008）。
- **推送台账 + 全局预算**：每条 Telegram 消息记入 `data/state/notify-ledger/<date>.jsonl`；即时信号走 `notify.send_signal`；预算当前 **observe（只记录不拦截）**，`config/notify-budget.yaml`；`make notify-report days=7` 看统计（ADR 0009）。
- **环境**：根 `.venv`（Python 3.9 + ruff/PyYAML）；无 venv 时回退 `python3`。

## 4. 常用命令

```bash
make gate                    # 提交前必跑
make test / make lint        # 只跑单测 / 只跑 ruff
make jobs                    # launchd 任务总览（只读）
make healthcheck module=<m> [fix=1] [notify=1]
make launchd module=<m> action=status|install|uninstall
make notify-report days=7    # 推送台账统计
make prune module=<m> [apply=1]
make new-module name=crypto-xxx desc="..."
tail -f <module>/data/logs/collect.log
```

模块内手动命令（`run.sh --dry-run` / `--status` / `--test` 等）见各模块 README。

## 5. 注意事项

1. 项目必须在 `~/vscode/crypto-research`：`~/Documents` 受 macOS TCC 保护，launchd 读不了。
2. **launchd 直接跑工作区代码**：切分支 / 改 `scripts/` 下一轮即生效；不要在仓库副本里跑 `collect.sh`（会触发 `healthcheck --fix` 装出幽灵任务），实验后跑 `make jobs` 检查。
3. 合盖休眠会暂停定时（夜间 70–90 分钟空档）；`make keep-awake` 可防休眠，要 7×24 需 VPS。
4. 数据不会无限增长：每日首轮自动 prune（规则见 `tools/prune.py`）。
5. 测试不得写生产 `data/`：gate 为通知台账与限速状态设置临时目录；新测试需要写状态时同样用临时目录。

## 6. 待办

1. **约 2026-11-23**：看 `make notify-report days=7`，定 `daily_signal_cap` 与是否切 `enforce`（需用户确认）。首日数据：即时信号 23 条/天，cap=10 时 07:00 前即耗尽，主要是 price 与 news 对 BTC 的近重复 P1。
2. 架构评审遗留：长函数拆分 + ruff 复杂度门禁；Python 3.9 → 3.12 升级（单独窗口）。
3. 模块业务待办（可选，按需排期）：
   - crypto-news：Phase 4 早警指标、LLM 三行摘要、周报校准（[方案 §5–§8](crypto-intel-plan.md)）；
   - crypto-price：关键位自动校准；
   - crypto-jobs：源扩充 / 薪资解析 / 技能共现（[design §P1](crypto-jobs/design.md)）；
   - crypto-knowledge：按反馈迭代课程（追加 `config/curriculum/stage*.yaml` 后跑 `check_content.py` + `build_index.py`）；某条实践反复被跳过 → 降级或拆分。
