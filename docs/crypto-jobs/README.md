# crypto-jobs · 文档

> 模块：加密行业技术岗位（DevOps/SRE/MLOps/开发）招聘市场情报｜代码：[`crypto-jobs/`](../../crypto-jobs/README.md)
> 状态：MVP 已实现（2026-11-08）

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 问题、边界（做/不做）、验收标准、依赖与风险 |
| [design.md](design.md) | 选源原则与合规、数据契约、技能抽取算法、指标定义、监测闭环、失败模式、演进 |
| [architecture.md](architecture.md) | 业务能力图、业务数据流、关键流程、监测闭环、与其他模块关系 |
| [technical.md](technical.md) | 模块技术架构：代码结构、组件职责、运行链路、状态/数据、测试、CLI |

## 一句话回答「怎么做」

1. **收集**：只抓公开、零成本源——加密公司官方 ATS 公开 JSON（Greenhouse/Lever/Ashby/Workable，T0）、
   加密垂直招聘站（T1）、带 crypto 过滤的通用远程站（T2）；不碰 LinkedIn/Indeed/Glassdoor。
2. **理解**：把 JD 归一化（含开发岗位：后端/前端/全栈/移动/区块链/数据/ML 等），用 `config/skills.yaml`
   的规则词典抽技能，并区分硬性要求/加分项。
3. **持续监测**：每天全量快照 → 与上一份/7 天前对比 → 周报给出技能 Top、周环比、升温/降温、新岗位、
   活跃公司；周日 10:00 推 Telegram；历史快照 + 原始数据保证可回溯、可换词典重算。

## 快速命令

```bash
cd crypto-jobs
./scripts/run.sh --dry-run --json     # 联网抓一轮，只打印（不写盘不推送）
./scripts/run.sh                      # 日更：写快照/索引/日报/exports
./scripts/run.sh --weekly             # 生成周报（读历史快照）
./scripts/run.sh --test --dry-run     # Telegram 通道 dry-run
python3 scripts/e2e.py                # 离线全链路回放（CI）
```
