# ADR 0005：模块名必须是领域词

- 状态：已采纳（2026-11-08）
- 背景：第一个模块最初命名为 `crypto-new`（占位名），随后按领域更名为 `crypto-news`，连带改 label/CI/文档 97 处引用。
- 决策：模块目录、label、文档标题一律使用领域词（news/price/onchain/macro…），禁止 new/temp/test 等占位名；脚手架与评审应拦截。
- 影响：新增模块按 `crypto-<领域>` 命名；发现占位名先提醒再动手。
