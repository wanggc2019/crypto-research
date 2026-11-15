# ADR 0009：全仓推送台账 + 全局即时信号预算

- 状态：已采纳（2026-11-16），默认 `observe`
- 背景：项目目标是「每天 ≤10 条可决策信号」，但推送上限分散在各模块（x-kol 8、onchain 10+5（特殊期 30）、price/news/policy 各自规则），且有多个独立 Bot；没有任何地方知道「今天总共打扰了几次」，同一事件（如大跌）可能被 price 异动、onchain 大额入所、news 快讯各推一条。
- 决策：
  1. **台账在传输层**：`crypto_core.notify.telegram.send_message/send_document` 每次发送自动记入 `data/state/notify-ledger/<date>.jsonl`，调用方只需（可选）传 `meta={module, kind, severity, assets, key}`；台账出错绝不影响发送。
  2. **预算只管即时信号**（`kind=signal`），统一出口 `send_signal()`：P0 永远放行；非 P0 计入全仓日预算 `daily_signal_cap`；同一关注资产在 `correlation_window_min` 内已有其它模块送达 → 跨模块重复。
  3. **三种模式**：`observe`（默认，只标注 would_suppress）→ 用 `make notify-report days=7` 看一周真实数据后再定上限与是否 `enforce`；`off` 关闭判定。
  4. 被拦截的信号对调用方返回 `(True, "suppressed: …")`（视为已处理，不重试）；内容仍在各模块日报中。
  5. 共享状态不与模块耦合：模块之间仍零 import，只依赖 libs + 仓库级 `data/state/` 与 `config/notify-budget.yaml`。
- 测试隔离：gate 为所有步骤设置临时 `CRYPTO_NOTIFY_LEDGER_DIR`；台账在 unittest 进程里未显式指定时写临时目录，防止测试污染生产计数。
- 不做：消息合并成一条（各模块卡片格式不同，先观察重复量再决定）；Bot 合并。
