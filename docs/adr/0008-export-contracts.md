# ADR 0008：跨模块 exports 契约登记表

- 状态：已采纳（2026-11-16）
- 背景：模块间只读对方 `data/exports/`，但路径和字段是各消费方手写的，生产方改动无人校验。实例：crypto-analyst 的周报与事件归档读的是不存在的 `crypto-kol/`（真实模块 `crypto-x-kol`），KOL 数据长期为空却无测试发现；crypto-onchain 的 price fixture 也落后于真实产出（缺 `source_health`）。
- 决策：
  1. `libs/crypto_core/src/crypto_core/contracts.py` 登记每个跨模块契约：生产模块、文件名、消费方依赖的必需字段（顶层 + 条目），只在模块内用的 schema 记入 `INTERNAL`；
  2. 消费方一律 `contracts.read(schema, date=, repo=)` 读取（缺失/损坏/schema 不符 → 降级为 default），不手拼路径；
  3. 标准样例 `contract_examples/*.json`，`write_example_repo()` 按真实目录铺假仓库供消费方测试；
  4. 三层测试：生产方（e2e/单测里校验真实产出）、消费方（样例假仓库里确实读到数据）、仓库级（schema 字面量都有登记、带 schema 的 fixture 都符合）。
- 演进：生产方只增字段不改语义；要改语义就新登记 `v2`，消费方迁移后再删 `v1`。
- 影响：新增跨模块读取或新 export 时，先在登记表登记，否则 `tools/tests/test_contracts_registry.py` 失败。
