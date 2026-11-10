# 设计：crypto-analyst —— 行情分析（研究 / 方法验证）

## 1. 总体方案

```
K 线缓存 / price exports / news exports
        │
        ▼
core.py（纯函数）：state_row / ma200_zone_stats / correlation / equal_weight_stats / Brier
        │
        ▼
analyze.py（终端）· daily_brief.py（简报）· weekly.py（周报）· score.py（复盘打分）
```

判断由人工写入研究记录（本地文件），脚本只读并做统计；**不保存任何个人资产数据**。

## 2. 分层与数据流

1. `fetch_history.py` 拉 4 币日线 → `data/raw/klines/`（幂等、断点续跑）；
2. `core.py` 纯函数计算状态表、条件统计、相关性、等权参考指标；
3. `analyze.py` 打印状态/统计（不写盘）；
4. `daily_brief.py` / `weekly.py` 生成简报与周报；
5. `score.py` 对到期判断做 Brier/方向打分（记录缺失视为空）。

## 3. 状态计算（`core.state_row`）

输入日线，输出：最新价、MA50/MA200、距均线百分比、区间位置、波动、回撤等；所有数值来自缓存，缺失返回空。

## 4. 条件统计（`core.ma200_zone_stats`）

按「价格相对 MA200 的位置」分组，统计后续 30/90/365 天收益分布；同时给**日级**与**时段级**两种样本，避免小样本误读。

## 5. 情景树

- 情景区间 `band_px`、概率（合计 100%）、触发条件、失效条件；
- 概率仅作研究假设，到期用价格回放检验（Brier + 方向）；
- 输出必须包含「证据 / 反证 / 不可知」三段。

## 6. 输出产物

- 每日简报：`data/reports/<date>-daily-brief.md`；
- 周报：`data/reports/<date>-weekly.md`；
- 终端分析：只打印；
- 复盘打分：回写研究记录（本地）。

## 7. 失败模式与降级

| 场景 | 行为 |
|---|---|
| K 线缓存缺失 | 跳过相关统计，简报降级 |
| price/news 导出缺失 | 该段标注不可用，不阻塞其余输出 |
| 概率/区间异常 | 标注并跳过该情景，不硬编 |

## 8. 合规

只做公开行情数据的统计分析与方法研究，不构成投资建议；不提供交易/出入金/账户操作指引；不保存任何个人资产信息。
