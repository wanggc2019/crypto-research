# 技术架构：crypto-analyst（模块内部）

## 1. 模块结构

```
crypto-analyst/
├── config/{app.yaml,analyst.yaml,analysis-report-template.md}
├── deploy/launchd/*.plist.tmpl
├── scripts/
│   ├── fetch_history.py   拉日线 → data/raw/klines/
│   ├── core.py            纯函数：状态/条件统计/相关性/Brier
│   ├── analyze.py         终端状态与统计（只打印）
│   ├── daily_brief.py     每日简报
│   ├── weekly.py          周报
│   ├── macro.py           DVOL/DXY/US10Y
│   ├── score.py           到期复盘打分
│   └── send_*.py          Telegram 发送
├── tests/                 离线单测 + fixtures
└── records/               人工研究记录（本地生成，.gitignore 不入库）
```

## 2. 数据契约

- 输入：本地 K 线缓存（`data/raw/klines/{btc,eth,bnb,sol}_1d.json`）、`crypto-price/data/exports/latest.json`、crypto-news 导出；
- 输出：`data/reports/<date>-daily-brief.md`、`<date>-weekly.md`、`data/exports/`（如配置）；
- 所有计算离线、纯函数优先；数据缺失降级，不编造。

## 3. 运行链路

```bash
./scripts/run.sh                      # 每日/手动入口
python3 scripts/fetch_history.py      # 首次取数（联网）
python3 scripts/daily_brief.py --dry-run
python3 scripts/weekly.py --dry-run
python3 -m unittest discover -s tests -v
```

## 4. 配置

`config/app.yaml`：时区、poll_seconds、Telegram 开关、输出路径；
`config/analyst.yaml`：分析参数（均线、情景区间等）。凭据只在仓库根 `.env`。

## 5. 测试与质量门

- `crypto-analyst/tests/` 离线单测（core / daily_brief / weekly / score / send_brief / fetch_history）；
- fixtures 位于 `tests/fixtures/`（K 线样例）；
- 由 `make gate` / CI 自动发现执行。

## 6. 退出码

0 正常；2 配置/凭据问题；3 全部源失败；其它为部分失败（记录到 errors，不阻塞）。

## 7. 合规

只做公开数据研究；不保存任何个人资产信息；不提供交易、出入金或账户操作指引。
