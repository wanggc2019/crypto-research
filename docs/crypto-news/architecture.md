# 业务架构：crypto-news —— 行业信息情报采集/评分/告警

> 需求见 [requirement.md](requirement.md)，实现细节见 [design.md](design.md)；仓库技术架构见 [../architecture.md](../architecture.md)。

## 1. 业务能力图

```mermaid
flowchart LR
    subgraph 输入["信息输入（分层）"]
        T0["T0 一手<br/>交易所/监管/项目官方"]
        T1["T1 专业媒体"]
        T2["T2 高管本人原创"]
        T3["T3 聚合/搬运<br/>只做线索"]
    end
    subgraph 处理
        F["硬规则过滤<br/>白名单/实体/防骗"]
        C["去重 & 聚类<br/>事件簇 + 独立源"]
        S["分类 + 评分<br/>S/T/M/C/D"]
        G["分级 + 质量门<br/>P0/P1/P2"]
    end
    subgraph 输出
        P0["P0 即时推送"]
        P1["P1 全天即时"]
        P2["P2 日报/归档"]
        EX["exports/<br/>跨模块只读"]
    end
    T0 --> F
    T1 --> F
    T2 --> F
    T3 --> F
    F --> C --> S --> G
    G --> P0
    G --> P1
    G --> P2
    G --> EX
    P0 --> TG["Telegram"]
    P1 --> TG
```

## 2. 业务数据流

```mermaid
flowchart LR
    SRC["44 个信源<br/>RSS/公告 API/WARN/美股/X/行情"] --> FETCH["fetch.py<br/>采集+归一化+源游标"]
    FETCH --> RAW[("raw/<日期>/items.jsonl")]
    RAW --> SCORE["score.py<br/>匹配→聚类→分类→评分→分级"]
    SCORE --> ALERTS[("state/alerts-<日期>.json")]
    SCORE --> REP[("reports/<日期>.md")]
    ALERTS --> NOTIFY["notify.py<br/>去重/即时送达/中文卡片"]
    NOTIFY --> TG["Telegram"]
    FETCH -. "当天首轮" .-> PRUNE["tools/prune.py<br/>保留期+体积上限"]
```

## 3. 告警生命周期

```mermaid
sequenceDiagram
    participant L as launchd(600s)
    participant C as collect.sh
    participant F as fetch/score
    participant N as notify
    participant T as Telegram
    participant U as 用户
    L->>C: 到点触发（锁）
    C->>F: 采集 + 归一化 + 评分分级
    F-->>C: alerts（P0/P1）+ 日报
    C->>N: --send（去重/送达判定）
    alt P0 或日间 P1
        N->>T: 中文卡片（要点/影响/动作）
        T-->>U: 推送
    end
    N-->>C: 更新 notified.json
    C->>C: healthcheck（静默；异常→自愈/告警/按需 AI）
```

## 4. 与其他模块 / 共享库的关系

| 关系 | 说明 |
|---|---|
| `libs/crypto_core` | `http`、`sources`（rss/announcements/telegram）、`market`（行情参考）、`events`、`dedupe`、`notify`、`store`、`env/config` |
| `crypto-price` | **零 import**；新闻只解释"为什么"，价格只回答"发生了什么"；两者共用 Telegram 凭据与 libs |
| 未来 `crypto-analyst` | 可读 `crypto-news/data/exports/`（事件快照）与 `crypto-price/data/exports/latest.json`，不读 state/raw |
| 调度 | launchd `com.crypto-research.crypto-news`（600s），每轮末内嵌 healthcheck，异常时按需调 `dsh headless` 巡检 |
