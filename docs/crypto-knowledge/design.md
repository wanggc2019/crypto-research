# 设计：crypto-knowledge

> 需求见 [requirement.md](requirement.md)；仓库级约定见 [../conventions.md](../conventions.md)。

## 1. 总体结构

```
launchd（13:00 / 19:00 / 21:00 / 23:00）
  └─ collect.sh（加锁 → run.sh --notify → healthcheck → prune）
       └─ main.py（选时段 → 选卡 → 渲染 → 发送 → 落状态/报告）
            ├─ content.py    加载 + 校验 config/curriculum/stage*.yaml
            ├─ schedule.py   游标、SRS、去重、内容耗尽兜底
            ├─ render.py     Telegram HTML 卡片
            └─ common.py     共享库适配（config/store/timeutil/notify）
```

## 2. 内容模型

一个文件 = 一个阶段，`stage1..4.yaml`：

```yaml
schema_version: 1
stage: 1
title: 基础：货币、账本、密码学与比特币
weeks: "W1–W4"
items:
  - id: c101            # c=概念 t=理论 p=实践
    type: concept       # concept | theory | practice
    stage: 1
    order: 1            # 概念 1–20；课程轨 1–20（理论/实践交错）
    title: 账本
    en: Ledger
    minutes: 4
    sections:
      - {h: 一句话, p: ...}
      - {h: 为什么重要, p: ...}
      - {h: 常见误解, p: ...}
    quiz: {q: ..., a: ...}
    sources: [https://bitcoin.org/bitcoin.pdf]
```

校验规则（`content.py`）：id 唯一且符合 `[ctp]\d{3}`、type 合法、stage 1–4、sections 2–5 节且 `p` 30–300 字、概念/理论必须有 quiz、sources ≤2 且为 http(s)。完整规则见 [config/curriculum/README.md](../../crypto-knowledge/config/curriculum/README.md)。

## 3. 选卡与间隔复习（SRS）

状态文件 `data/state/progress.json`：

```json
{
  "schema_version": 1,
  "created": "2026-11-10",
  "cursors": {"concept": 3, "lesson": 2},
  "items": {"c101": {"first_on": "2026-11-10", "last_on": "2026-11-10", "reps": 1, "next_due": "2026-11-11"}},
  "sent": {"2026-11-10": {"13": ["c101"], "19": ["t101"], "23": []}},
  "history": [{"date": "2026-11-10", "slot": "13", "ids": ["c101"], "mode": "new"}]
}
```

- **新内容**：概念/课程两条轨道各自一个游标，按 `(stage, order)` 顺序取。
- **复习**：首次发送后 `next_due = +1 天`；之后每次复习按 `[1,3,7,14,30,60]` 递进。23:00 取 `next_due <= 今天` 的条目，最多 2 条；当天新发的两条不重复计入。
- **去重**：`sent[date][slot]` 存在即跳过；`--force` 按已发 id 重渲染同一条，不推进游标/SRS。
- **兜底**：概念/课程池耗尽 → 转间隔复习；课程优先循环实践条目；永不发空消息。
- **容错**：状态损坏/版本不符 → 回退空状态；发送失败不推进状态。

## 4. 消息渲染

- 统一页眉：`【crypto-knowledge】` + `🏷 任务：<launchd Label>` + 阶段 + 第 N 天。
- 概念课（10–12 分钟）：6 节 + 2–3 道自测（答案 23:00 复盘课揭晓）+ 延伸阅读。
- 理论/实践课（12–15 分钟）：理论 5–6 节、实践 5 节；均带 2–3（实践 1–2）道自测 + 延伸阅读。
- **今日回顾与总结**（21:00，约 8 分钟）：今日课程清单 + 每条 2 个关键要点 + 进度（第 N 天 / 已学 / 待复习 / 连续天数）+ 一句话总结任务 + 明日预告；**不推进 SRS**。
- **本周回顾与总结**（周日 21:00，约 10 分钟）：本周课程（按天）、关键要点、本周数据（学习天数/连续/已学/待复习/阶段进度）、本周自测 3 题（折叠答案）、下周预告；由 `weekly.enabled/weekday` 控制，`--weekly` 可强制预览。
- 睡前复盘课（约 12 分钟）：今日两条各 2 题主动回忆（`<tg-spoiler>` 点开看答案）+ 间隔复习 4 条以内 + 综合思考题 + 明日预告。
- 所有文本先 `html.escape`；链接只显示「延伸阅读」；总长 ≤ `message.max_chars`（默认 3500，Telegram 上限 4096）。

## 5. 时段与调度

| 时段 | 行为 | 说明 |
|---|---|---|
| 13:00 | 概念课 | 正常通知 |
| 19:00 | 理论/实践课 | 正常通知 |
| 21:00 | 回顾与总结（周日：本周回顾） | 正常通知 |
| 23:00 | 睡前复盘课 | 正常通知（不静默） |

`main.py --slot auto`：先补发「今天已到点但还没发」的时段，再按当前窗口（12–15 / 18–20 / 20–22 / 22–24）映射；Mac 睡醒后错过的时段会补发，**不再跳过**。launchd 模板固定四个 `StartCalendarInterval`。

## 6. 产物与日志

- `data/state/progress.json`：学习状态（gitignored）。
- `data/reports/<date>.md`：当天三条消息存档（人读，长期保留）。
- `data/logs/collect.log`：`===== start =====` / `fetch: ...` / `done (rc=N)`，供 `tools/healthcheck.py` 解析。
- `data/logs/crypto-knowledge.log`：发送成功/失败的明细。

## 7. 失败模式

| 场景 | 行为 |
|---|---|
| 专用 Bot 未配置 | 回退共享 Bot（`--whoami` 会标注“回退共享 Bot”） |
| Telegram 凭据缺失/格式错 | 退出码 2/3，不推进状态，日志记录原因 |
| 网络失败 | 退出码 3，下轮重试同一条 |
| 状态文件损坏 | 回退空状态（会在日志体现） |
| 内容校验失败 | 退出码 2，不发送；CI/`check_content.py` 拦截 |
| 同一时段并发 | `collect.sh` 目录锁，后到者跳过（exit 0） |
| 内容耗尽 | 复习模式；仍无内容才报错 |

## 8. 测试

`tests/`：内容校验、选卡/SRS、渲染/转义/截断、端到端（fixtures + 临时 data 目录 + mock 发送）。
全部离线；`make gate` / CI 均会执行。
