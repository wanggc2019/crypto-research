# crypto-news · 文档

> 模块：行业信息情报（采集→过滤聚类→评分分级→Telegram 告警/日报）｜代码：[`crypto-news/`](../../crypto-news/README.md)

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 目标、关注范围、边界（做/不做）、验收标准 |
| [design.md](design.md) | 五段式流水线、信源分层、评分模型、告警分级与去重、数据契约 |
| [architecture.md](architecture.md) | 信息供应链、业务数据流、告警生命周期、与共享库/其他模块关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构、组件职责、运行链路、状态/数据、测试 |

## 运行 / 检查周期

| 项 | 周期 |
|---|---|
| 运行 | launchd 每 **600 秒**（单轮 20–40 秒，单实例锁） |
| 自检 | **每轮结束** `healthcheck --quiet`（正常静默；25 分钟未成功运行判失败；告警 60 分钟去重） |
| AI 深度分析 | 仅异常时，≥ **6 小时**冷却；**后台异步**执行（不阻塞主任务），单次超时 600 秒，结束后发修复报告 |
| 每日维护 | 当天首轮 `prune`（raw 90d / logs 30d / reports 365d / alerts 180d / exports 7d / 单日志 2MB / data 500MB 上限） |
| 送达 | 全天即时（2026-11-10 起取消夜间静默，P0/P1 都立即推送） |

深度方案（历史版）：[crypto-intel-plan.md v0.6](../crypto-intel-plan.md)
状态：**运行中**（launchd `com.crypto-research.crypto-news`，StartInterval=600）
