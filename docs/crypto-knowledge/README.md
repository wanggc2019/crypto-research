# docs/crypto-knowledge

> 需求目录索引，对应代码目录 [`crypto-knowledge/`](../../crypto-knowledge/README.md)。

| 文档 | 回答什么问题 |
|---|---|
| [requirement.md](requirement.md) | 为什么做、用户已确认偏好、做什么/不做什么、验收标准、风险 |
| [design.md](design.md) | 内容模型、SRS 算法、渲染规则、调度窗口、失败模式、测试 |
| [architecture.md](architecture.md) | 业务能力图、数据流、一天四班的时序图、内容阶段 |
| [technical.md](technical.md) | 代码结构、组件职责、运行链路、状态/数据、CLI/退出码 |

内容格式与写作规则：[`crypto-knowledge/config/curriculum/README.md`](../../crypto-knowledge/config/curriculum/README.md)。

## 状态（2026-11-09）

- 模块已实现：160 条课程内容（概念 80 / 理论 45 / 实践 35），4 个阶段。
- 每天四班：13:00 概念课 / 19:00 理论或实践课 / **21:00 今日回顾与总结（约 8 分钟；周日为本周回顾）** / 23:00 复盘课（10–12 分钟）。
- 间隔复习：第 1/3/7/14/30/60 天回流；内容耗尽自动转复习模式。
- 调度：launchd `com.crypto-research.crypto-knowledge`，三个 `StartCalendarInterval`。
- 测试：68 个离线单测；`make gate` 与 CI 覆盖。
- 关联方案：[个人学习方案 v0.2](../crypto-learning-plan.md)。
