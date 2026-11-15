# crypto-policy 运维记录

> 本文件记录 `crypto-policy` 的运行质量复盘与优化项；代码/设计仍以 requirement / design / technical 为准。

## 2026-11-15 · 上线首周复盘（2026-11-08 ~ 2026-11-15）

### 1. 运行概览

| 指标 | 值 |
|---|---|
| 调度 | 单 launchd 任务，每 1 小时 |
| 完整轮次 | 160 轮 |
| rc=0 / rc=1 / rc=3 | 107 / 53（33% 降级） / 0 |
| 最近一轮 | 2026-11-15 22:02，rc=0，14 源 |
| 数据量 | 11 MB（`crypto-policy/data/`） |
| 消息时间 | 2026-11-10 起所有 Telegram 消息页脚带本地时区标记（见 docs/notify-policy.md） |

### 2. 产出

- **法规库**：15 条 —— P0 5 / P1 4 / P2 6；地区 US 12、UK 1、CN 2、未标注 2（历史记录）。
- **新增节奏**：11-09 ~ 11-10 集中入库；11-11 起 0 新增（低频 + 部分源失效，见 §3）。
- **速报推送**：0 条（首周 P0/P1 多为首次基线，或落在旧的静默时段排队；见 §4-5）。
- **AI 解读草稿**：5 篇已推送（`data/reports/interpret-*.md`，3.5–6.3 KB）；
  均含原文引用、旧规关系、影响、风险提示与「非法律意见」；质量可用。
- **实际发到 Telegram 的消息**：5 篇 AI 解读草稿；速报卡片 0 条（缺口见 §4-5）。

### 3. 数据源健康（首周 fetch 日志 + collect.log）

| 源 | 轮次 | 列表失败 | found | 备注 |
|---|---:|---:|---:|---|
| pbc_tiaofasi / pbc_news / safe_zcfg / court_sfjs | 8 | 0 | 160/120/160/160 | 稳定 |
| csrc_guizhang | 8 | 2 | 120 | collect.log 中 31 次 timeout（约 19%） |
| eurlex_search | 7 | 1 | 3 | 16 次 Forbidden + 1 次 SSL hostname mismatch |
| cftc_press | 7 | 0 | **0** | RSS 接口已返回 HTML → 0 条，**静默失效** |
| esma_news | 7 | 0 | **0** | 同上，**静默失效** |
| fsa_jp_press | 7 | 0 | 14 | 首周无 crypto 命中 |
| sec_press / fed_press / ecb_press / fca_news / federal_register | 7 | 0 | 175/140/105/140/280 | 正常 |
| gongbao 2 源 / mas_news | — | — | — | 已 `enabled:false` 备用 |

> 结论：33% 的 rc=1 主要来自 csrc timeout（31 次）与 eurlex Forbidden（16 次）；
> 更严重的是 **CFTC / ESMA 两个源 found 恒为 0，且被当成“正常空列表”**，存在漏检。

### 4. 质量问题

1. **CFTC / ESMA RSS 失效但静默**（高）：`_load_rss` 对非 XML 内容返回 `[]`，没有报错，healthcheck 也看不到。
2. **EUR-Lex 间歇 403 / 证书校验失败**（中）：16 次 Forbidden；1 次 `CERTIFICATE_VERIFY_FAILED: Hostname mismatch`。
3. **证监会超时**（中）：列表 15s 超时、无重试；约 19% 轮次超时，但下一轮常恢复。
4. **分类误判**（中）：`2026-20430` 实为 **撤回通知（withdrawal）**，因 Federal Register `type=Proposed Rule` 被映射成 P1 拟议规则；AI 解读报告已自行纠正。
5. **速报卡片缺口**（中）：旧静默时段排队的 4 条 pending 后来只走了 AI 解读，未补发速报；`data/state/notified.json` 未生成（无速报推送成功记录）。
6. **P2 噪音**（低）：6/15 是 SRO ETF 通知 / Agency Information Collection 公告；只归档不推送，但稀释法规库。
7. **历史地区字段缺失**（低）：2 条国内记录 `jurisdiction` 为空（字段上线前的旧数据）。
8. **首周“无新增”不可解释**（观察）：11-11 起 0 新增，既可能是低频，也可能是源失效；修源前不能下结论。

### 5. 优化建议（按优先级）

**P0（本周内）**

1. 修 CFTC / ESMA：换 HTML 列表适配器或更新 RSS URL；`_load_rss` 增加 content-type/XML 校验，解析 0 条即抛错并计入 errors。
2. EUR-Lex：重试 + 退避；持续 403 时标记为 optional（不拖累 rc），并记录到 health。
3. 证监会：`list_timeout` 提到 25s、`list_retries: 2`，保留 1.2s 请求间隔。
4. Federal Register：`withdrawal` / `correction` / `extension` 标题映射为 `notice`（P2），不再当拟议规则。
5. 速报补发：每轮对比法规库中「未 notified 且非 stale」的 P0/P1，补发一次速报（幂等，写入 `notified.json`）。

**P1（下周）**

6. P2 过滤：`Self-Regulatory Organizations` 与 `Agency Information Collection Activities` 只归档到单独视图/周报，不进主法规库。
7. `jurisdiction` 缺省回填为 `CN`（迁移一次 index）。
8. 源健康：某源连续 N 轮 `found=0` 或 `list_ok=false` → healthcheck WARN/Telegram。
9. 可用性验证：香港 SFC/HKMA、新加坡 MAS 备用源重新探测。
10. 观察两周：统计新规命中率、误报率、AI 解读可读性；若仍无新增，先排查源覆盖而非调阈值。

### 6. 下次复盘检查项

- [ ] CFTC / ESMA `found > 0` 且无静默失败；
- [ ] rc=1 比例降到 <10%；
- [ ] 速报与 AI 解读成对出现，`notified.json` 有记录；
- [ ] 无 `found=0` 的“正常”源；
- [ ] P2 噪音占比下降（目标 <30%）。
