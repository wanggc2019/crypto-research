# 业务架构：{{name}} —— {{desc}}

> 需求见 [requirement.md](requirement.md)，实现细节见 [design.md](design.md)。
> 仓库技术架构（分层/调度）见 [../architecture.md](../architecture.md)。

## 1. 业务能力图

```mermaid
flowchart LR
    IN["输入<br/>（数据源）"] --> PROC["处理<br/>（规则/算法）"]
    PROC --> OUT["输出<br/>（告警/报告）"]
    OUT --> USER["用户<br/>（Telegram）"]
```

## 2. 业务数据流

```mermaid
flowchart LR
    A["数据源"] --> B["采集/归一化"]
    B --> C["规则/算法"]
    C --> D["告警/报告"]
    D --> E["送达/归档"]
```

## 3. 关键流程 / 生命周期

```mermaid
sequenceDiagram
    participant S as 数据源
    participant M as {{name}}
    participant U as 用户
    S->>M: 定时拉取
    M->>M: 判定/去重/冷却
    M-->>U: 达到阈值 → Telegram
```

## 4. 与其他模块 / 共享库的关系

| 关系 | 说明 |
|---|---|
| `libs/crypto_core` | 复用（列出用到的能力） |
| 其他需求模块 | 零 import；如需数据只读对方 `data/exports/` |
| 调度 | launchd Label `com.crypto-research.{{name}}` |
