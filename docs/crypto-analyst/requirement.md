# 需求：crypto-analyst —— 行情分析（研究 / 方法验证）

## 1. 要解决什么问题

用一个**可复跑、可回溯、可复盘**的流程回答三个问题：
1. 现在处于什么状态（价格/均线/波动/区间位置）；
2. 历史上类似状态后来怎样（条件统计，日级与时段级并列）；
3. 未来 1–3 个月可能怎么走（概率 + 触发 + 失效条件的情景树），到期按 Brier/方向命中复盘。

## 2. 边界：做什么 / 不做什么

### 做
- 只读公开数据：本地 K 线缓存 + `crypto-price/data/exports/latest.json` + `crypto-news` 导出；
- 状态表、条件统计、情景区间、复盘打分、每日/每周简报；
- 输出方向 + 条件 + 证据，可证伪。

### 不做
- 不采集、不抓新闻、不重算价格（由 price/news 模块负责）；
- 不提供交易/出入金/账户操作指引；不处理任何个人资产或交易数据；
- 不做日内、杠杆/合约；标的仅 BTC/ETH/BNB/SOL。

## 3. 输出与频率

| 输出 | 频率 | 形态 |
|---|---|---|
| 每日简报 | 每日 18:30 | Markdown + Telegram |
| 周报 | 周日 22:00 | Markdown（本地） |
| AI 综合分析 | 周日 22:00 | Markdown + Telegram |

## 4. 数据源

- 本地 K 线缓存 `crypto-analyst/data/raw/klines/`（Binance 日线，2019-01-01 起）；
- `crypto-price/data/exports/latest.json`（价格/资金费率/OI，只增字段）；
- `crypto-news` 导出（事件情报，P0/P1）；
- 可选宏观：Deribit DVOL、DXY、US10Y。

## 5. 验收标准

- `python3 -m unittest discover -s crypto-analyst/tests` 全绿；
- `daily_brief.py --fixtures ... --dry-run` 离线回放结构正确；
- 输出含免责声明，不含个人资产信息；
- `scripts/analyze.py` 离线可跑、只打印不写盘。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 过拟合/小样本 | 条件统计标注样本量；不把历史胜率当保证 |
| 结论误导 | 固定输出「证据 + 失效条件 + 不可知」 |
| 数据缺口 | 缓存缺失时降级/跳过，不编造数值 |
| 合规 | 只做公开数据研究；不提供交易指引 |
