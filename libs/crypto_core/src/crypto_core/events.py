# -*- coding: utf-8 -*-
"""统一告警事件模型 `alert/v1`。

模块负责产出事件（决定阈值/分级/路由），libs 只负责结构与去重键。
为兼容现有落盘格式，`event_key()` 直接复用「首标题归一化 + sha256[:24]」。
"""
from dataclasses import dataclass, field

from . import text, timeutil

SCHEMA = "alert/v1"
SEVERITIES = ("P0", "P1", "P2")

# 旧模块用过 critical/warn/info，保留映射便于跨模块统一
LEVEL_BY_SEVERITY = {"critical": "P0", "warn": "P1", "info": "P2"}
SEVERITY_BY_LEVEL = {"P0": "critical", "P1": "warn", "P2": "info"}


def event_key(alert):
    """去重键：只看事件标题本身，不含评分/来源数/时间等每轮会变的字段。"""
    if isinstance(alert, dict):
        title = (alert.get("titles") or [alert.get("title") or ""])[0]
    else:
        title = getattr(alert, "title", "")
    return text.sha256(text.norm_title(title))[:24]


def severity_of(alert):
    """取事件的统一分级 P0/P1/P2（容忍旧字段 level / severity）。"""
    sev = alert.get("severity") if isinstance(alert, dict) else getattr(alert, "severity", None)
    if sev in SEVERITIES:
        return sev
    level = alert.get("level") if isinstance(alert, dict) else getattr(alert, "level", None)
    if str(level).upper() in SEVERITIES:
        return str(level).upper()
    return LEVEL_BY_SEVERITY.get(str(level).lower(), "P2")


@dataclass
class Alert:
    """统一告警事件。落盘/推送前用 to_dict() 得到可 JSON 序列化结构。"""

    module: str
    severity: str
    category: str
    title: str
    assets: list = field(default_factory=list)
    detail: list = field(default_factory=list)
    urls: list = field(default_factory=list)
    ts: str = ""
    dedupe_key: str = ""
    score: dict = None
    summary: dict = None
    evidence: list = field(default_factory=list)
    schema: str = SCHEMA

    def __post_init__(self):
        self.ts = self.ts or timeutil.ts_iso()
        if self.severity not in SEVERITIES:
            self.severity = LEVEL_BY_SEVERITY.get(str(self.severity).lower(), "P2")
        self.dedupe_key = self.dedupe_key or event_key({"title": self.title})

    def to_dict(self):
        return {
            "schema": self.schema,
            "module": self.module,
            "severity": self.severity,
            "category": self.category,
            "assets": self.assets,
            "title": self.title,
            "detail": self.detail,
            "urls": self.urls,
            "ts": self.ts,
            "dedupe_key": self.dedupe_key,
            "score": self.score,
            "summary": self.summary,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            module=d.get("module", ""),
            severity=severity_of(d),
            category=d.get("category", ""),
            title=d.get("title") or (d.get("titles") or [""])[0],
            assets=list(d.get("assets") or []),
            detail=list(d.get("detail") or []),
            urls=list(d.get("urls") or ([d["url"]] if d.get("url") else [])),
            ts=d.get("ts", ""),
            dedupe_key=d.get("dedupe_key", ""),
            score=d.get("score"),
            summary=d.get("summary"),
            evidence=list(d.get("evidence") or []),
        )


# ---------------------------------------------------------------- 展示词汇（中文）
# 事件类别 / 硬条件 / 影响 / 动作参考：展示层共用的领域词表。
# 模块只产出英文枚举，展示时用下面的函数翻译，避免每条消息各写一套。
CATEGORY_CN = {
    "protocol_security": "协议安全",
    "exchange_custody": "交易所托管",
    "stablecoin_depeg": "稳定币脱锚",
    "regulatory": "监管执法",
    "macro": "宏观",
    "tokenomics": "代币经济",
    "capital_flow": "资金流",
    "tech_upgrade": "技术升级",
    "technical_upgrade": "技术升级",
    "governance": "治理",
    "market_structure": "市场结构",
    "industry_labor": "组织动向",
    "other": "其他",
}

HARD_CN = {
    "stablecoin_depeg": "稳定币脱锚",
    "exchange_withdrawal": "交易所暂停提币",
    "hack_large": "大额被盗（>$1000万）",
    "etf_resolution": "ETF 正式决议",
    "sec_head_exchange": "头部交易所被监管",
    "fomc_surprise": "FOMC 意外",
    "liquidation_cascade": "清算瀑布（>$5亿）",
    "executive_legal": "高管法律事件",
}

IMPACT_CN = {
    "protocol_security": "安全事件，短期压制风险偏好，相关资产/生态可能承压",
    "exchange_custody": "交易对手风险，涉及资产可能短期无法提取，优先核对敞口",
    "stablecoin_depeg": "稳定币失锚可能引发连锁清算，系统性风险前兆",
    "regulatory": "监管变化影响全市场 beta，关注后续执法范围",
    "macro": "宏观变量影响整体流动性与风险偏好",
    "tokenomics": "供给变化影响单币供需结构",
    "capital_flow": "资金流向变化，关注是否持续",
    "tech_upgrade": "技术节点影响链上可用性与市场预期",
    "technical_upgrade": "技术节点影响链上可用性与市场预期",
    "governance": "治理进展影响项目方向与市场预期",
    "market_structure": "杠杆/流动性结构变化，注意波动放大",
    "industry_labor": "人员/组织变化可能影响交易所执行与市场预期，未证实前只作线索",
    "other": "影响待评估，先记录",
}

ACTION_CN = {
    "protocol_security": "先核对官方公告与链上数据，检查相关 DeFi/合约敞口",
    "exchange_custody": "检查该所敞口，暂缓大额充提，等官方说明",
    "stablecoin_depeg": "检查稳定币敞口与借贷仓位，关注是否扩散",
    "regulatory": "关注执法对象与范围，评估对全市场的影响",
    "macro": "关注流动性与美股联动，不追涨杀跌",
    "tokenomics": "核对解锁/增发节奏，评估供给压力",
    "capital_flow": "看是否连续多日同向，单日数据不构成趋势",
    "tech_upgrade": "关注链上状态与升级窗口，避免在窗口内大额操作",
    "technical_upgrade": "关注链上状态与升级窗口，避免在窗口内大额操作",
    "governance": "关注投票结果与执行时间",
    "market_structure": "注意杠杆水平与清算风险，控制仓位",
    "industry_labor": "关注是否被官方或主流媒体证实，不据此单独交易",
    "other": "观察，不做动作",
}


def category_cn(cat):
    return CATEGORY_CN.get(cat, cat or "其他")


def hard_cn(trigger):
    return HARD_CN.get(trigger, trigger)


def impact_cn(cat):
    return IMPACT_CN.get(cat, IMPACT_CN["other"])


def action_cn(cat):
    return ACTION_CN.get(cat, ACTION_CN["other"])
