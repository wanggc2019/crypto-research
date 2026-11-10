# crypto-analyst · 行情分析（研究 / 方法验证）

> 状态：可用（2026-11-08 起）。只对**公开行情数据**做统计分析，不涉及任何真实资金或个人资产信息。
> 输出为研究方法与案例推演，**不构成投资建议**。

## 定位

把公开行情压缩成可证伪的判断：**状态 → 条件统计 → 情景（概率+触发+失效）→ 到期复盘打分**；不做喊单、不产生交易动作。

## 文档

| 文档 | 内容 |
|---|---|
| [requirement.md](requirement.md) | 需求、边界、验收标准 |
| [design.md](design.md) | 数据流、状态/条件统计、情景树与打分口径 |
| [architecture.md](architecture.md) | 业务能力图与数据流（Mermaid） |
| [technical.md](technical.md) | 脚本结构、数据契约、配置、测试、合规 |
| [report-format.md](report-format.md) | 报告格式规范 |

## 运行

```bash
python3 crypto-analyst/scripts/fetch_history.py   # 拉 4 币日线
python3 crypto-analyst/scripts/analyze.py         # 状态 + 条件统计
python3 crypto-analyst/scripts/daily_brief.py --dry-run
python3 crypto-analyst/scripts/weekly.py --dry-run
python3 -m unittest discover -s crypto-analyst/tests -v
```
