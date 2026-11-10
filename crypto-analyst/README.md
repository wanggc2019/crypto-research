# crypto-analyst · 行情分析（研究 / 方法验证）

> **定位**：只读其他模块的 `data/exports/`，对**公开行情数据**做统计分析，产出「状态 → 评估 → 情景（概率 + 触发 + 失效条件）」与复盘打分。
> **不构成投资建议**：不提供交易、出入金、账户操作指引；不涉及任何真实资金或个人资产数据。
> **合规**：中国境内对虚拟货币相关业务活动有明确监管限制；本模块仅做公开数据研究与方法验证。

## 1. 做什么

- 状态表：四币（BTC/ETH/BNB/SOL）价格、均线、区间位置、波动；
- 条件统计：MA200 等状态下的历史后续收益分布（日级 / 时段级并列）；
- 情景：给方向 + 触发 + 失效条件（可证伪），不写点位/杠杆；
- 复盘：到期后按 Brier / 方向命中打分（人工写判断、脚本打分）；
- 每日 18:30 简报、周日 22:00 周报（本地文件 / Telegram）。

## 2. 数据与命令

```bash
python3 scripts/fetch_history.py            # 拉 4 币日线（2019-01-01 起），幂等缓存
python3 scripts/analyze.py                  # 状态表 + 条件统计
python3 scripts/daily_brief.py              # 每日简报 → data/reports/<date>-daily-brief.md
python3 scripts/daily_brief.py --dry-run    # 只打印
python3 scripts/macro.py                    # DVOL / DXY / US10Y
python3 scripts/weekly.py                   # 行情分析周报（本地文件）
./scripts/ai_analysis.sh                    # AI 周度综合分析（dsh headless）
python3 scripts/send_brief.py --dry-run     # 精炼 Telegram 简报预览
python3 scripts/send_brief.py --notify      # 发送
```

离线回放（CI 用，无需网络）：

```bash
python3 scripts/daily_brief.py --fixtures tests/fixtures --date 2026-11-08 --dry-run
python3 -m unittest discover -s tests -v
```

缓存位置 `data/raw/klines/{btc,eth,bnb,sol}_1d.json`（`data/` 已 gitignore）。

## 3. 边界

- 只读 `crypto-price/data/exports/latest.json` 与 `crypto-news` 导出，不采集、不抓新闻、不重算价格；
- 不保存任何个人资产或交易数据；
- 输出为公开数据的统计分析与方法说明，**不构成投资建议**。

## 4. 文档

- [需求](../../docs/crypto-analyst/requirement.md) · [设计](../../docs/crypto-analyst/design.md) · [架构](../../docs/crypto-analyst/architecture.md) · [技术](../../docs/crypto-analyst/technical.md)
