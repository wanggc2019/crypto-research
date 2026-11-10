# 文档导航

> 仓库文档分两类：**共享层**（跨需求）与**需求目录**（每个需求一个子目录，名字与代码目录一致）。

## 共享层（跨需求）

| 文档 | 内容 |
|---|---|
| [../AGENTS.md](../AGENTS.md) | **Agent 开工入口**：开工检查、实现/测试/PR 硬规则、常见坑、`.agents/skills/` 索引 |
| [architecture.md](architecture.md) | **仓库技术架构**：目录分层、依赖方向、运行调度、共享库/契约（含结构图） |
| [runbook.md](runbook.md) | 运行与下线：launchd 机制、暂停/卸载/彻底下线、健康检查、故障排查 |
| [notify-policy.md](notify-policy.md) | 消息发送规范（即时类 vs 总结类、发送窗口） |
| [conventions.md](conventions.md) | 命名 / 配置 / 数据 / CLI / 退出码 / 文档规划规范 |
| [dev-workflow.md](dev-workflow.md) | 开发工作流与 **PR 规范**（标题格式 / 分支 / 验证 / 收尾） |
| [adr/](adr/) | 架构决策记录（0001-0007） |
| [crypto-intel-plan.md](crypto-intel-plan.md) | 消息面方案 v0.6（crypto-news 的深度业务方案，历史版） |
| [crypto-learning-plan.md](crypto-learning-plan.md) | **个人学习方案 v0.3**：长期碎片化路线、知识地图、10–15 分钟课程设计、合规边界 |
| [HANDOFF.md](HANDOFF.md) | 交接文档：当前状态、常用命令、TODO |
| [code-plan-module-split.md](code-plan-module-split.md) · [day1-setup.md](day1-setup.md) · [history/](history/) | 历史记录 |

## 需求目录（每个需求一个子目录）

目录名 = 代码目录名，例如 `docs/crypto-news/` ↔ `crypto-news/`。每个目录固定五件套：

| 文件 | 回答什么问题 |
|---|---|
| `README.md` | 这个需求是什么、状态、代码/调度入口、其余文档索引 |
| `requirement.md` | 为什么做、做什么/不做什么、触发条件、验收标准、依赖、风险 |
| `design.md` | 怎么做：数据源、算法/规则、接口契约、产物、测试方案、失败模式 |
| `architecture.md` | **业务架构图**（Mermaid）：业务能力、业务数据流、关键流程、模块关系 |
| `technical.md` | **模块技术架构**：本模块代码结构、组件职责、运行链路、状态/数据、测试 |

> 边界：**仓库级**技术架构（分层/依赖/调度）统一在 [architecture.md](architecture.md)，各需求的 `technical.md` 只写本模块内部实现，不重复画全仓图。

## 索引

| 需求 | 状态 | 文档目录 | 代码 | 运行 / 检查周期 |
|---|---|---|---|---|
| crypto-news · 行业信息情报 | 运行中 | [docs/crypto-news/](crypto-news/README.md) | [`crypto-news/`](../crypto-news/README.md) | 600s 运行 · 每轮自检 · 异常≥6h 才 AI |
| crypto-price · 价格异动告警 | 运行中 | [docs/crypto-price/](crypto-price/README.md) | [`crypto-price/`](../crypto-price/README.md) | 60s 运行 · 每轮自检 |
| crypto-analyst · 行情分析 / 方法验证 | P2 主体完成 | [docs/crypto-analyst/](crypto-analyst/README.md) | [`crypto-analyst/`](../crypto-analyst/README.md) | 每日简报 · 每周情景树 · 复盘打分 |
| crypto-x-kol · KOL 的 X 推文监控 | 运行中 | [docs/crypto-x-kol/](crypto-x-kol/README.md) | [`crypto-x-kol/`](../crypto-x-kol/README.md) | 300s 抓取（26 个账号）· 每轮自检 · 全天即时 |
| crypto-policy · 国内政策/法规/案例雷达 | MVP 运行中 | [docs/crypto-policy/](crypto-policy/README.md) | [`crypto-policy/`](../crypto-policy/README.md) | 3600s 抓取（14 源：国内 5 + 国外 9）· 规则门 · P0/P1 推送 · 首次基线不推 |
| crypto-jobs · 加密行业技术岗位市场情报 | MVP 已实现 | [docs/crypto-jobs/](crypto-jobs/README.md) | [`crypto-jobs/`](../crypto-jobs/README.md) | 每日 18:00 全量快照/日报 · 周日 10:00 周报 · 每 2 小时新岗位速报 |
| crypto-onchain · 链上/交易所卖压归因 | M2 + 链上大额转账（影子） | [docs/crypto-onchain/](crypto-onchain/README.md) | [`crypto-onchain/`](../crypto-onchain/README.md) | 300s 影子采集 + 归因 + 链上大额转账（≥$5M）· 只落盘不推送 · exports `onchain/pressure/v1` |
| crypto-knowledge · 每日 4 条学习内容 | 运行中 | [docs/crypto-knowledge/](crypto-knowledge/README.md) | [`crypto-knowledge/`](../crypto-knowledge/README.md) | 13:00 / 19:00 / 21:00 / 23:00 四班 · 间隔复习 1/3/7/14/30/60 天 · 160 条内容 |

## 新增需求

```bash
make new-module name=crypto-onchain desc="链上异动监控"
```

会同时生成代码骨架 `crypto-onchain/` 与文档骨架 `docs/crypto-onchain/`（五件套，从 [_template/](_template/) 复制）。
填完后把本页索引、根 README 模块表、CI、Makefile 各加一行即可。

## 模板

[_template/](_template/) — 占位符 `{{name}}` / `{{desc}}` / `{{date}}`。
