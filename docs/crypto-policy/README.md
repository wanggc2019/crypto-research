# crypto-policy · 文档

> 模块：国内数字货币政策/法规/案例雷达 + 法规库 + 解读草稿｜代码：[`crypto-policy/`](../../crypto-policy/README.md)

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 问题、边界（做/不做）、触发与分级、验收标准、依赖与风险 |
| [design.md](design.md) | 数据源、规则门、法规库、接口契约、产物、测试、失败模式 |
| [architecture.md](architecture.md) | 业务能力图、业务数据流、关键流程、与其他模块/共享库关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构、组件职责、运行链路、状态/数据、测试 |

状态：**已上线**（2026-11-08，2026-11-09 增加国外源）。国内覆盖 5 个可用 T0/T1 源
（央行条法司/新闻、外汇局、证监会规章、最高法司法解释；公报 2 源备用）；
国外覆盖 9 个源（7 个机构 RSS + Federal Register 规则原文 + EUR-Lex 法规检索；MAS 备用），规则门 + 法规库 + 速报 + 离线回放 +
**AI 解读（事件驱动：检测到新政策立即触发 DSH headless）** 已跑通；最高检/案例库因 403/需登录暂不接入。
