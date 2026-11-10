# 需求：crypto-news —— 行业信息情报采集/评分/告警

> 状态：已实现并运行（2026-11-08 起）｜模块目录：[`crypto-news/`](../../crypto-news/README.md)
> 关联：[设计文档](design.md) · [业务架构图](architecture.md) · [方案 v0.6](../crypto-intel-plan.md) · [HANDOFF](../HANDOFF.md)

## 1. 要解决什么问题（一句话）

把分散、嘈杂、真假混杂的加密信息，压缩成**每天不超过 10 条、每条能回答"这事跟我仓位有什么关系"**的信号，通过 Telegram 送达。

## 2. 边界：做什么 / 不做什么

### 做

- 关注范围：**BTC / ETH / BNB**（SOL、ASTER 冻结；其他山寨不看）。
- 信源：交易所公告、监管（SEC/CFTC/Fed 等）、项目官方、一线媒体、**头部交易所创始人/CEO 本人原创**（CZ、何一、Brian Armstrong、Ben Zhou…）。
- 处理：硬规则过滤 → 去重/聚类 → 11 类事件分类（含组织动向）→ 评分（S/T/M/C/D）→ P0/P1/P2 分级。
- 美股/宏观（市场级事件）：作为 **P2 背景/日报**，最多 P1（不 P0）；英文标题自动附中文解读。
- 组织动向（交易所裁员/重组/高管离职）：作为 **P2 线索**进日报；信源包括官方 WARN 备案（T0）、Google News（T3）与 Reddit 社区（经 Google 索引，T3）；T3 只作线索，不单源 P0。
- 告警：P0/P1 全天即时、P2 日报/归档（不设夜间静默，用户 2026-11-10 要求）；同事件 24h 去重。
- 产物：本地日报 + `alerts-<date>.json` + 原始 `raw` 留存（可回放）。
- 零成本：本机 Python 3.9 标准库 + 免费公开源，不买 API/VPS。

### 不做

- 不做交易/下单/仓位管理；不接私钥与交易所私有 API。
- 不做价格预测，不监控广义 KOL（只盯高管本人原创）；广义 KOL 推文由独立模块
  [`crypto-x-kol`](../crypto-x-kol/README.md) 负责，本模块不重复抓取（两边账号集合由
  `tools/tests/test_account_overlap.py` 保证 0 交集）。
- 不重复 crypto-price 的实时行情告警（本模块只在评分中参考行情数据）。
- 不接付费数据源（Glassnode/CoinGlass 等先不引入）。

## 3. 验收标准（可测量）

1. `make verify` 全绿（ruff + libs 单测 + 模块 52 项单测 + 端到端重评分）。
2. 采集：43 个 `verified` 源一轮成功率 ≥ 90%，失败单源不拖垮整轮。
3. 评分：`data/reports/<date>.md` 输出 P0/P1/P2 分级，`alerts-<date>.json` 与推送内容一致。
4. 去重：同一事件重跑不重复推送；夜间 P1 不推送；T3 聚合站不计独立源。
5. 推送：`notify.py --test` 能收到 Telegram；卡片中文、含类别/来源/影响/动作参考。
6. 调度：launchd 每 600s 一轮；日志 `data/logs/collect.log` 有 start/done(rc=0)；带锁防重叠。
7. 数据不失控：daily prune + 500MB 硬上限生效。

## 4. 依赖的共享能力

`libs/crypto_core`：paths/env/config/store/timeutil/text、http、sources（rss/announcements/telegram）、market（行情数据）、events、dedupe、notify。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 噪音/标题党被抬成 P0 | 硬条件需「已确认（T0 或 ≥2 非 T3 独立源）+ 48h 内 + 衰减 D≥0.1」；T3 不计独立源 |
| 聚合站/搬运号当一手源 | 信源分层（T0–T3）+ 卡片标注「未确认/聚合站」 |
| 源改版/限流 | 单源失败继续、状态留存、可回放 raw 调试 |
| 中文媒体无 RSS | Google News / r.jina.ai 代理兜底 |
| Mac 休眠漏跑 | launchd 唤醒后补跑；Runbook 给出 VPS 后路 |
| 数据/日志膨胀 | 每日 prune（raw 90d / logs 30d / reports 365d / alerts 180d）+ 单日志 2MB + 总量 500MB 上限 |
