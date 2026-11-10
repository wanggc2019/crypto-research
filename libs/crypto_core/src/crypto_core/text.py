# -*- coding: utf-8 -*-
"""文本工具：清洗、URL 规范化、哈希、关键词匹配。"""
import hashlib
import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def sha256(s):
    return hashlib.sha256(str(s).encode("utf-8", "ignore")).hexdigest()


def clean_text(s):
    """去 HTML 标签、反转义实体、压缩空白。"""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", str(s))
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def norm_url(u):
    """URL 规范化：去 utm_*/ref/fbclid/gclid 参数与尾斜杠。"""
    if not u:
        return ""
    u = str(u).strip()
    u = re.sub(r"[?&](utm_[^=&]+|ref|fbclid|gclid)=[^&]*", "", u)
    return u.rstrip("/")


def contains_any(text, words):
    """text 是否命中 words 中任意一个（大小写不敏感）。"""
    t = (text or "").lower()
    return any(w and str(w).lower() in t for w in words)


def norm_title(t, limit=80):
    """标题归一化：去标点/大小写/空白差异，用于跨轮次稳定去重。"""
    t = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()[:limit]

# ---------------------------------------------------------------- 英文标题中文解读（规则版，零依赖）
def _fmt_usd(value, unit):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if unit.lower() == "billion":
        n *= 10  # $1 billion = 10 亿美元
        return ("%.1f" % n).rstrip("0").rstrip(".") + " 亿美元"
    n /= 100  # $100 million = 1 亿美元
    return ("%.2f" % n).rstrip("0").rstrip(".") + " 亿美元"


def _cn_fragments():
    return [
        (r"etf (?:inflow|outflow)s? hit (?:a )?record", lambda m: "ETF 净流入创纪录" if "inflow" in m.group(0) else "ETF 净流出创纪录"),
        (r"etf (inflow|outflow)s?", lambda m: "ETF 净流入" if m.group(1).lower() == "inflow" else "ETF 净流出"),
        (r"(?:drops?|falls?|slides?)[, ]+then (?:pops?|rebounds?|recovers?)", "先跌后涨"),
        (r"(?:loses|falls? below|drops? below|breaks? below|below)\s+(\d+)[- ](week|day|month)[- ]moving average",
         lambda m: "跌破 %s %s均线" % (m.group(1), {"week": "周", "day": "日", "month": "月"}[m.group(2).lower()])),
        (r"(?:(24-hour|24h)\s+)?crypto\s+long liquidations? (?:top|tops|exceed|exceeds|surge past)\s*\$?([\d.]+)\s*(billion|million)",
         lambda m: "%s加密多头清算超 %s" % (("24 小时" if m.group(1) else ""), _fmt_usd(m.group(2), m.group(3)))),
        (r"fed (?:cuts?|cutting) (?:interest )?rates?", "美联储降息"),
        (r"fed (?:hikes?|raising) (?:interest )?rates?", "美联储加息"),
        (r"cpi (?:cools|eases|falls)", "CPI 降温"),
        (r"cpi (?:hot|hotter|rises|surges)", "CPI 升温"),
        (r"all[- ]time high", "创历史新高"),
        (r"all[- ]time low", "创历史新低"),
        (r"record high", "创历史新高"),
        (r"record low", "创历史新低"),
        (r"funding rate", "资金费率"),
        (r"open interest", "持仓量"),
        (r"perps?|perpetual futures", "永续合约"),
        (r"(?:tech|technology)[- ](?:stocks?|selloff|sell-off)", "科技股抛售"),
        (r"weighs? on crypto majors", "拖累加密主流币"),
        (r"stock market|wall street|equities", "美股"),
        (r"circuit breaker", "熔断"),
        (r"rate cut", "降息"),
        (r"rate hike", "加息"),
        (r"fomc", "FOMC"),
        (r"\bfed\b", "美联储"),
        (r"\bcpi\b", "CPI"),
        (r"\b(?:binance|coinbase|okx|bybit|kraken|bitget)\b\s+(?:hack(?:ed|s)?\s+)?launderers?", "交易所黑客洗钱者"),
        (r"mint(?:s|ed|ing)?\s+.*?memecoins?", "铸造迷因币"),
        (r"move\s+stolen\s+funds?", "转移被盗资金"),
        (r"homemade[^,]+,\s*animal memecoins?", "自制马斯克/动物主题迷因币"),
        (r"hack(?:ed|s|ing)?", "黑客攻击"),
        (r"launder(?:ing|ers?|ed)?", "洗钱"),
        (r"stolen funds?|stolen (?:crypto|assets?|money)", "被盗资金"),
        (r"memecoins?", "迷因币"),
        (r"\bmint(?:s|ed|ing)?\b", "铸造"),
        (r"exploits?|exploited", "漏洞利用"),
        (r"breach(?:ed)?", "数据泄露"),
        (r"fraud", "欺诈"),
        (r"lawsuit|sued", "诉讼"),
        (r"settlement", "和解"),
        (r"bankrupt(?:cy)?", "破产"),
        (r"depeg(?:ged)?|脱锚", "脱锚"),
        (r"stablecoins?", "稳定币"),
        (r"whales?", "巨鲸"),
        (r"airdrops?", "空投"),
        (r"unlocks?", "解锁"),
        (r"halving", "减半"),
        (r"reserves?", "储备"),
        (r"withdrawals?|withdraw", "提现"),
        (r"elliptic", "Elliptic 分析"),
        (r"inflation", "通胀"),
        (r"recession", "衰退"),
        (r"tariffs?", "关税"),
        (r"geopolitical|iran talks", "地缘政治"),
        (r"(?:cheating|spy|fraud)[- ]scandal", "丑闻"),
        (r"volatility", "波动加剧"),
        (r"liquidations?", "清算"),
        (r"selloff|sell-off|rout", "抛售"),
        (r"crash|plunge|slump|tumble", "大跌"),
        (r"rally|surge|soar|jump", "大涨"),
    ]


_CN_WORDS = [
    (r"\bnet income\b", "净利润"), (r"\bquarterly\b", "季度"), (r"\bearnings\b", "财报"),
    (r"\brevenue\b", "营收"), (r"\btreasury\b", "财库"), (r"\bholdings?\b", "持仓"),
    (r"\bbuys?\b|\bbought\b", "买入"), (r"\bsells?\b|\bsold\b", "卖出"),
    (r"\bstaking\b|\bstake\b", "质押"), (r"\bshares?\b", "股份"),
    (r"\bprice target\b", "目标价"), (r"\bupgrade[sd]?\b", "上调评级"),
    (r"\bdowngrade[sd]?\b", "下调评级"), (r"\bmarket cap\b", "市值"),
    (r"\bpartnership\b", "合作"), (r"\bacquisition\b", "收购"),
    (r"\blist(?:s|ed|ing)?\b", "上线"), (r"\bdelist(?:s|ed|ing)?\b", "下架"),
    (r"\bpayments?\b", "支付"), (r"\bcustody\b", "托管"), (r"\bcharter\b", "牌照"),
    (r"\bapproval\b|\bapproved\b", "批准"), (r"\bproposal\b", "提案"),
    (r"\bexploit(?:s|ed)?\b", "漏洞利用"), (r"\bhackers?\b|\bhack(?:s|ed)?\b", "黑客攻击"),
    (r"\blaunder(?:ing|ers?|ed)?\b", "洗钱"), (r"\bmint(?:s|ed|ing)?\b", "铸造"),
    (r"\bmemecoins?\b", "迷因币"), (r"\bstolen\b", "被盗"), (r"\bfunds?\b", "资金"),
    (r"\bliabilit(?:y|ies)\b", "负债"), (r"\bfine[ds]?\b", "罚款"),
    (r"\blawsuit\b", "诉讼"), (r"\bsettlement\b", "和解"), (r"\binvestigation\b", "调查"),
    (r"\bbankrupt(?:cy)?\b", "破产"), (r"\bdefault\b", "违约"),
    (r"\brates? cut\b|\bcut rates?\b", "降息"), (r"\brates? hike\b|\bhike rates?\b", "加息"),
    (r"\binflation\b", "通胀"), (r"\brecession\b", "衰退"), (r"\btariffs?\b", "关税"),
    (r"\bcircuit breaker\b", "熔断"), (r"\bvolatility\b", "波动"),
    (r"\bliquidations?\b", "清算"), (r"\bopen interest\b", "持仓量"),
    (r"\bfunding rate\b", "资金费率"), (r"\bperps?\b|\bperpetual\b", "永续合约"),
    (r"\betfs?\b", "ETF"), (r"\bstablecoins?\b", "稳定币"), (r"\bdepeg(?:ged)?\b", "脱锚"),
    (r"\bwhales?\b", "巨鲸"), (r"\bairdrops?\b", "空投"), (r"\bunlocks?\b", "解锁"),
    (r"\bhalving\b", "减半"), (r"\breserves?\b", "储备"),
    (r"\bwithdrawals?\b|\bwithdraw\b", "提现"), (r"\bdeposits?\b", "充值"),
    (r"\bplunge[sd]?\b|\bcrash(?:es|ed)?\b|\bslump[s]?\b", "暴跌"),
    (r"\brall(?:y|ies|ied)\b|\bsurge[sd]?\b|\bsoar(?:s|ed)?\b", "大涨"),
    (r"\bfalls?\b|\bfell\b|\bdrops?\b|\bdeclines?\b", "下跌"),
    (r"\brises?\b|\brose\b|\bgains?\b|\bjumps?\b", "上涨"),
    (r"\bhits?\b|\breaches?\b", "达到"), (r"\bunveils?\b|\blaunch(?:es|ed)?\b", "发布"),
    (r"\bannounces?\b|\bannounced\b", "宣布"), (r"\bnew\b", "新"), (r"\breports?\b|\breported\b", "报道"),
    (r"\bbitcoin\b", "BTC"), (r"\bethereum\b", "ETH"), (r"\bbinance\b", "币安"),
    (r"\bnvidia\b", "英伟达"), (r"\bfed\b|\bfederal reserve\b", "美联储"),
    (r"\bcpi\b", "CPI"), (r"\bfomc\b", "FOMC"), (r"\bsec\b", "SEC"),
    (r"\belliptic\b", "Elliptic 分析"), (r"\bunrealized\b", "未实现"),
    (r"\bgains?\b", "收益"), (r"\bether\b", "ETH"), (r"\bon\b", "因"), (r"\bcoindesk\b", "CoinDesk"),
]


def _cn_glossary(text):
    """词级直译兜底：把常见词替换成中文，保留数字/专名。"""
    t = text
    # 金额：$751 m / $1.28 billion
    def money(m):
        n = float(m.group(1))
        unit = m.group(2).lower()
        if unit == "b":
            return ("%.1f" % (n * 10)).rstrip("0").rstrip(".") + " 亿美元"
        return ("%.2f" % (n / 100)).rstrip("0").rstrip(".") + " 亿美元"
    t = re.sub(r"\$?\s*([\d.]+)\s*(billion|million|b|m)\b", money, t, flags=re.I)
    for pattern, cn in sorted(_CN_WORDS, key=lambda x: -len(x[0])):
        t = re.sub(pattern, cn, t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,.:;")
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    return t if re.search(r"[\u4e00-\u9fff]", t) else ""


def cn_summary(title):
    """把常见英文财经标题压成一句中文解读；非英文/无匹配返回空串。"""
    t = clean_text(title or "")
    if not t or re.search(r"[\u4e00-\u9fff]", t):
        return ""
    low = t.lower()
    asset = "BTC" if "bitcoin" in low else ("ETH" if "ethereum" in low else "")
    pieces = []
    for pattern, repl in _cn_fragments():
        m = re.search(pattern, t, re.I)
        if not m:
            continue
        piece = repl(m) if callable(repl) else repl
        if not piece:
            continue
        if any(piece in p or p in piece for p in pieces):
            continue
        compact = piece.replace(" ", "")
        if any(len(compact) >= 2 and
               any(compact[i:i + 2] in p.replace(" ", "") for i in range(len(compact) - 1))
               for p in pieces):
            continue
        if piece in ("创历史新高", "创历史新低") and any(p.startswith("ETF") for p in pieces):
            continue
        pieces.append(piece)
    if not pieces:
        gloss = _cn_glossary(t)
        if gloss:
            return gloss[:120]
        # 兜底：扫常见术语，至少给出中文关键词
        terms = [
            ("binance", "币安"), ("coinbase", "Coinbase"), ("nvidia", "英伟达"),
            ("hack", "黑客攻击"), ("launder", "洗钱"), ("stolen", "被盗资金"),
            ("memecoin", "迷因币"), ("mint", "铸造"), ("exploit", "漏洞利用"),
            ("etf", "ETF"), ("liquidation", "清算"), ("stablecoin", "稳定币"),
            ("depeg", "脱锚"), ("whale", "巨鲸"), ("airdrop", "空投"),
            ("unlock", "解锁"), ("halving", "减半"), ("bankruptcy", "破产"),
            ("lawsuit", "诉讼"), ("settlement", "和解"), ("tariff", "关税"),
            ("recession", "衰退"), ("fed", "美联储"), ("cpi", "CPI"),
        ]
        kws = []
        for key, cn in terms:
            if key in low and cn not in kws:
                kws.append(cn)
        if not kws:
            return ""
        return "关键词：%s" % "、".join(kws[:5])
    text = "；".join(pieces[:4])
    if asset and "BTC" not in text and "ETH" not in text:
        text = asset + " " + text
    return text[:120]

_BLOCK_RE = re.compile(r"(?is)<(script|style|noscript|svg|template|head)[^>]*>.*?</\1>")
_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")


def html_to_text(raw):
    """HTML/字节 → 可见文本：去注释、script/style/svg 等块、标签，压缩空白。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    s = str(raw or "")
    s = _COMMENT_RE.sub(" ", s)
    s = _BLOCK_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def looks_like_junk_page(text):
    """页面变更检测的垃圾过滤：CSS 变量/JS/JSON-LD 或有效文本太少时返回 True。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) < 200:
        return True
    low = t.lower()
    markers = ("--chakra-", "var(--", "function(", "@context", "webpack", "document.", "window.", "/*!")
    if sum(1 for m in markers if m in low) >= 1:
        return True
    letters = sum(1 for ch in t if ch.isalpha())
    return letters / max(1, len(t)) < 0.5

