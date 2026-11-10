# crypto-analyst 报告格式规范（v1）

## 1. 标题格式

`【crypto-analyst】<类型> · <日期>`，例如 `【crypto-analyst】每日简报 · 2026-11-08`。

## 2. 币种写法

BTC / ETH / BNB / SOL（全大写；不写中文别名）。

## 3. 必备字段

每条分析必须包含：
- 状态（价格、均线、区间位置）；
- 判断（方向/条件/证据）；
- 失效条件（什么情况下判断作废）；
- 数据口径与免责声明（不构成投资建议）。

## 4. 长度与频率

- 每日简报：≤ 900 字；
- 周报：≤ 2000 字；
- 频率：每日 18:30 / 周日 22:00。

## 5. 输出位置

- 本地：`crypto-analyst/data/reports/<date>-daily-brief.md`、`<date>-weekly.md`；
- Telegram：由 `send_brief.py` 发送（测试消息需标注“样例”）。
