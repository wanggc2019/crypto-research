# crypto-futures · 文档

> 模块：合约持仓结构（多空比）监控与告警｜代码：[`crypto-futures/`](../../crypto-futures/README.md)

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 问题、边界（做/不做）、验收标准、依赖与风险 |
| [design.md](design.md) | 数据源、算法/规则、接口契约、产物、测试、风险 |
| [architecture.md](architecture.md) | 业务能力图、业务数据流、关键流程、与其他模块/共享库关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构、组件职责、运行链路、状态/数据、测试 |

状态：**M1 已上线（2026-11-17）**；**M2 已实现（2026-11-17）**：crypto-price 的资金费率 / OI 告警迁入为 `funding_extreme` / `oi_spike`（[需求](requirement-m2.md)）；**多头出清提醒 `long_flush` 已实现、待评审（2026-11-18，[需求](requirement-flush.md)）**
