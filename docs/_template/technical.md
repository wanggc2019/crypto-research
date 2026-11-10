# 技术架构：{{name}}（模块内部）

> 业务架构见 [architecture.md](architecture.md)；需求见 [requirement.md](requirement.md)；实现见 [design.md](design.md)。
> **仓库级**技术架构见 [../architecture.md](../architecture.md)。

## 1. 模块结构

```
{{name}}/
├── scripts/         # 入口 + 业务逻辑（按数据/规则/送达分层，纯函数可离线测试）
├── config/          # 阈值/规则/白名单（业务策略）
├── tests/           # 离线单测 + fixtures
├── deploy/launchd/  # 调度模板
└── data/            # raw/state/reports/exports/logs（gitignored）
```

## 2. 组件职责与数据契约

| 阶段 | 文件 | 输入 | 输出 |
|---|---|---|---|

## 3. 运行链路

```
launchd → collect.sh（锁） → 主流程 → 推送/落盘 → 健康检查
```

## 4. 依赖（libs/crypto_core）

## 5. 状态与数据

## 6. 测试

## 7. 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
