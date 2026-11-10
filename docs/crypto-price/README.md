# crypto-price · 文档

> 模块：价格/衍生品异动实时告警（每 60 秒）｜代码：[`crypto-price/`](../../crypto-price/README.md)

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 问题、边界（做/不做）、验收标准、依赖与风险 |
| [design.md](design.md) | 数据源与取舍、告警规则/分级/去重、产物结构、模块内部接口契约 |
| [architecture.md](architecture.md) | 业务能力图、数据流、告警生命周期、与其他模块/共享库关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构、组件职责、运行链路、状态/数据、测试 |

## 运行 / 检查周期

| 项 | 周期 |
|---|---|
| 运行 | launchd 每 **60 秒**（单轮 2–6 秒，单实例锁，未结束则跳过） |
| 自检 | **每轮结束** `healthcheck --quiet`（正常静默；25 分钟未成功运行判失败；告警 60 分钟去重） |
| 推送冷却 | 按规则 15–120 分钟；同向扩大 ≥1.5 倍可再报（加剧）；P1 不设日限额 |
| 周报 | 每周日 **20:00** 数据周报（crypto-analyst），20:05 AI 复盘（DSH 自动化任务） |
| 日报 | 每天 **10:00**（）生成并推送；周报见 [`crypto-analyst/`](../../crypto-analyst/README.md)（周日 20:00） |
| 每日维护 | 当天首轮 `prune`（raw 90d / logs 30d / reports 365d / alerts 180d / exports 7d / 单日志 2MB / data 500MB 上限） |
| 静默 | 无；P0/P1 全天即时发送 |

状态：**运行中**（launchd `com.crypto-research.crypto-price`，StartInterval=60）
