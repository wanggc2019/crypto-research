# 业务架构：crypto-price —— 主流币价格/衍生品异动实时告警

> 需求见 [requirement.md](requirement.md)，实现细节见 [design.md](design.md)；仓库技术架构见 [../architecture.md](../architecture.md)。

## 1. 业务能力图

```mermaid
flowchart LR
    subgraph 输入
        M["多交易所公开行情<br/>Binance / OKX / Coinbase<br/>现货 + 永续"]
    end
    subgraph 处理
        I["指标计算<br/>多窗口涨跌/放量/OI/资金费率"]
        R["规则判定<br/>阈值/点位/数据质量"]
        G["告警治理<br/>去重/冷却/加剧/限额"]
    end
    subgraph 输出
        P0["P0 立即推送"]
        P1["P1 全天即时"]
        P2["P2 归档"]
        REP["每日报告 + exports 快照"]
    end
    M --> I --> R --> G
    G --> P0
    G --> P1
    G --> P2
    R --> REP
    P0 --> TG["Telegram"]
    P1 --> TG
```

## 2. 业务数据流

```mermaid
flowchart LR
    EX["交易所 REST<br/>（免密钥）"] --> SRC["sources.py<br/>三源中位数 + 价差核验"]
    SRC --> IND["indicators.py<br/>快照 + K 线窗口"]
    IND --> RULES["rules.py<br/>事件（含 dedupe_key/severity）"]
    RULES --> DEL["delivery.py<br/>去重/冷却/静默/限额"]
    DEL --> TG["Telegram<br/>P0/P1"]
    DEL --> ST[("state/<br/>delivery·alerts·market_state")]
    RULES --> RUN["run.py<br/>单实例锁 / 退出码 / 每日清理"]
    RUN --> REP[("reports/<br/>每日报告")]
    SRC --> EXP[("exports/latest.json<br/>跨模块只读契约")]
```

## 3. 告警生命周期

```mermaid
sequenceDiagram
    participant L as launchd(60s)
    participant R as run.py
    participant S as sources/indicators
    participant G as rules
    participant D as delivery
    participant T as Telegram
    participant U as 用户
    L->>R: 到点触发（单实例锁）
    R->>S: 拉取三源行情 + K 线
    S-->>R: 快照（含数据质量标记）
    R->>G: evaluate(snapshot, state)
    G-->>R: 事件列表（P0/P1/P2 + 去重键）
    R->>D: 去重/冷却/送达与限额判定
    alt 需要推送
        D->>T: 中文卡片
        T-->>U: P0/P1 立即（全天即时）
    end
    D-->>R: 更新 state + alerts 审计
    R-->>L: 退出码 0/1/3
```

## 4. 与其他模块 / 共享库的关系

| 关系 | 说明 |
|---|---|
| `libs/crypto_core` | `http`、`market.binance/okx/coinbase`、`events`、`notify`、`store`、`timeutil`、`env/config` |
| `crypto-news` | **零 import**；两者只共用 libs 与 Telegram 凭据；价格只回答"发生了什么"，不解释"为什么"（新闻解释由 crypto-news 负责） |
| 未来 `crypto-analyst` | 只读 `crypto-price/data/exports/latest.json`（`price/snapshot/v1`）与 `crypto-news/data/exports/`，不读对方 state/raw |
| 调度 | launchd `com.crypto-research.crypto-price`（`StartInterval=60`） |
