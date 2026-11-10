#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skills.py —— JD 技能抽取（规则驱动 / 可解释 / 确定性 / 离线）。

对外契约（main.py 依赖，勿改）：
    tax = load()                    # 默认读 crypto-jobs/config/skills.yaml
    ids(tax); extract(jd_text, tax) # {skills, required, counts, by_category}
    category_of("kubernetes"); label_of("kubernetes")

aliases 大小写不敏感、词边界匹配（中文适用），短/歧义词由 skills.yaml 的显式 regex 兜底；
required 取 Requirements/Qualifications/What you'll need/You have/Must have/任职要求 等标题后的正文，
到下一个标题（含 Nice to have）或空行块结束，找不到则 []。纯函数、无网络、无 LLM、确定性。
"""
import html
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE_ROOT = os.path.dirname(_HERE)  # crypto-jobs/
_REPO_ROOT = os.path.dirname(_MODULE_ROOT)
_LIBS_SRC = os.path.join(_REPO_ROOT, "libs", "crypto_core", "src")
if _LIBS_SRC not in sys.path:
    sys.path.insert(0, _LIBS_SRC)

from crypto_core import config as core_config  # noqa: E402

CONFIG_DIR = os.path.join(_MODULE_ROOT, "config")
DEFAULT_TAXONOMY_PATH = os.path.join(CONFIG_DIR, "skills.yaml")

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 匹配边界：不用 \b（中文/标点边界不可靠），统一用“前后不是字母数字下划线”。
_LB = r"(?<![A-Za-z0-9_])"
_RB = r"(?![A-Za-z0-9_])"


# ---------------------------------------------------------------- 分类法加载

def _validate(data, path):
    """校验并归一化 skills.yaml；结构错误直接抛 ValueError（配置错要早暴露）。"""
    if not isinstance(data, dict):
        raise ValueError("%s: 技能分类法必须是映射" % path)
    if data.get("schema_version") != 1:
        raise ValueError("%s: schema_version 必须为 1，实际 %r" % (path, data.get("schema_version")))
    raw_categories = data.get("categories")
    raw_skills = data.get("skills")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("%s: categories 不能为空" % path)
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ValueError("%s: skills 不能为空" % path)

    categories = []
    category_ids = set()
    for item in raw_categories:
        if not isinstance(item, dict):
            raise ValueError("%s: category 必须是映射" % path)
        cid = str(item.get("id") or "")
        label = str(item.get("label") or "")
        if not _ID_RE.match(cid):
            raise ValueError("%s: 非法 category id %r" % (path, cid))
        if cid in category_ids:
            raise ValueError("%s: category id 重复: %s" % (path, cid))
        if not label:
            raise ValueError("%s: category %s 缺少 label" % (path, cid))
        category_ids.add(cid)
        categories.append({"id": cid, "label": label})

    skills = []
    skill_ids = set()
    for item in raw_skills:
        if not isinstance(item, dict):
            raise ValueError("%s: skill 必须是映射" % path)
        sid = str(item.get("id") or "")
        label = str(item.get("label") or "")
        cid = str(item.get("category") or "")
        aliases = item.get("aliases") or []
        regex = item.get("regex")
        if not _ID_RE.match(sid):
            raise ValueError("%s: 非法 skill id %r" % (path, sid))
        if sid in skill_ids:
            raise ValueError("%s: skill id 重复: %s" % (path, sid))
        if not label:
            raise ValueError("%s: skill %s 缺少 label" % (path, sid))
        if cid not in category_ids:
            raise ValueError("%s: skill %s 的 category 不存在: %r" % (path, sid, cid))
        if not isinstance(aliases, list):
            raise ValueError("%s: skill %s 的 aliases 必须是列表" % (path, sid))
        normalized = []
        for alias in aliases:
            alias = str(alias).strip()
            if alias and alias not in normalized:
                normalized.append(alias)
        if not normalized:
            raise ValueError("%s: skill %s 缺少 aliases" % (path, sid))
        if regex is not None and not isinstance(regex, str):
            raise ValueError("%s: skill %s 的 regex 必须是字符串" % (path, sid))
        skill = {"id": sid, "label": label, "category": cid, "aliases": normalized}
        if regex and regex.strip():
            skill["regex"] = regex.strip()
        skills.append(skill)
        skill_ids.add(sid)

    raw_drilldown = data.get("drilldown") or {}
    if not isinstance(raw_drilldown, dict):
        raise ValueError("%s: drilldown 必须是映射" % path)
    drilldown = {}
    for parent, children in raw_drilldown.items():
        pid = str(parent)
        if pid not in skill_ids:
            raise ValueError("%s: drilldown 的大项 id 不存在: %r" % (path, pid))
        if not isinstance(children, list):
            raise ValueError("%s: drilldown.%s 必须是列表" % (path, pid))
        normalized_children = []
        for child in children:
            cid = str(child)
            if cid not in skill_ids:
                raise ValueError("%s: drilldown.%s 的子技能不存在: %r" % (path, pid, cid))
            if cid not in normalized_children:
                normalized_children.append(cid)
        drilldown[pid] = normalized_children

    return {"schema_version": 1, "categories": categories, "skills": skills,
            "drilldown": drilldown}


def load(taxonomy_path=None):
    """读取并校验分类法，返回 dict（含 schema_version/categories/skills）。"""
    path = taxonomy_path or DEFAULT_TAXONOMY_PATH
    data = core_config.load_yaml(path)
    if not data:
        raise FileNotFoundError("技能分类法不存在或为空: %s" % path)
    return _validate(data, path)


# ---------------------------------------------------------------- 匹配器编译

def _alias_pattern(alias):
    """alias → 大小写不敏感、词边界安全的正则片段；连字符等价于空白。"""
    words = [word for word in re.split(r"[\s\-]+", str(alias or "").strip()) if word]
    if not words:
        return ""
    body = r"\s+".join(re.escape(word) for word in words)
    last = words[-1]
    # 英文名词复数容错：validators / wallets；不改动以 s 结尾或非纯字母词（k8s/c++ 等）。
    if last.isascii() and last.isalpha() and len(last) >= 3 and not last.endswith("s"):
        body += r"(?:s|es)?"
    return _LB + body + _RB


def _triggers(aliases):
    """别名的小写触发串（跳过整段不存在的技能，纯性能优化；命中时正则必命中）。"""
    out = []
    for alias in aliases:
        trigger = " ".join(str(alias).lower().replace("-", " ").split())
        if trigger and trigger not in out:
            out.append(trigger)
    return tuple(out)


def _compile(taxonomy):
    """每个技能一条正则（aliases 忽略大小写 OR 显式 regex），返回 (skill, pattern, triggers)。

    triggers=None 表示必须全量扫描（含显式 regex 的技能无法安全预筛）。
    """
    entries = []
    for skill in taxonomy["skills"]:
        pieces = []
        aliases = [p for p in (_alias_pattern(a) for a in skill["aliases"]) if p]
        if aliases:
            pieces.append("(?i:" + "|".join(aliases) + ")")
        if skill.get("regex"):
            pieces.append("(?:" + skill["regex"] + ")")
        if not pieces:
            raise ValueError("技能 %s 既无可用 aliases 也无 regex" % skill["id"])
        try:
            pattern = re.compile("|".join(pieces))
        except re.error as exc:
            raise ValueError("技能 %s 的匹配规则非法: %s" % (skill["id"], exc)) from exc
        triggers = None if skill.get("regex") else _triggers(skill["aliases"])
        entries.append((skill, pattern, triggers))
    return entries


_LAST = {"taxonomy": None, "entries": None, "by_id": None}
_DEFAULT = {"taxonomy": None}


def _compiled_for(taxonomy):
    """按对象身份缓存编译结果（Lead 每轮只 load 一次，extract 会被调用很多次）。"""
    if _LAST["taxonomy"] is taxonomy:
        return _LAST["entries"], _LAST["by_id"]
    entries = _compile(taxonomy)
    by_id = {skill["id"]: skill for skill in taxonomy["skills"]}
    _LAST.update({"taxonomy": taxonomy, "entries": entries, "by_id": by_id})
    return entries, by_id


def _default_taxonomy():
    taxonomy = _DEFAULT["taxonomy"]
    if taxonomy is None:
        taxonomy = load()
        _DEFAULT["taxonomy"] = taxonomy
    return taxonomy


def ids(taxonomy=None):
    """全部技能 id（taxonomy 顺序）。"""
    taxonomy = taxonomy if taxonomy is not None else _default_taxonomy()
    return [skill["id"] for skill in taxonomy["skills"]]


def _skill_of(skill_id):
    _entries, by_id = _compiled_for(_default_taxonomy())
    skill = by_id.get(str(skill_id))
    if skill is None:
        raise KeyError("未知技能 id: %s" % skill_id)
    return skill


def category_of(skill_id):
    return _skill_of(skill_id)["category"]


def label_of(skill_id):
    return _skill_of(skill_id)["label"]


# ---------------------------------------------------------------- 文本预处理

_LI_OPEN_RE = re.compile(r"<\s*li\b[^>]*>", re.I)
_LI_CLOSE_RE = re.compile(r"<\s*/\s*li\s*>", re.I)
_BLOCK_TAG_RE = re.compile(
    r"<\s*/?\s*(?:p|div|ul|ol|tr|td|th|table|section|article|header|footer|h[1-6]|br|hr)\b[^>]*>",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]{1,400}>")
_URL_RE = re.compile(r"(?:https?://|ftp://|www\.)[^\s<>\"')\]]+", re.I)
_INTERNAL_HYPHEN_RE = re.compile(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])")
_INLINE_WS_RE = re.compile(r"[ \t\u00a0]+")
_NL_RE = re.compile(r"\n{2,}")


def _prepare_text(text):
    """去 HTML/URL、反转义、连字符归一化、保留行结构；HTML 列表项转成 "- " 行首标记。"""
    if not text:
        return ""
    value = str(text)
    value = _LI_OPEN_RE.sub("\n- ", value)
    value = _LI_CLOSE_RE.sub("\n", value)
    value = _BLOCK_TAG_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _URL_RE.sub(" ", value)
    value = value.replace("\u00a0", " ")
    value = _INTERNAL_HYPHEN_RE.sub(" ", value)  # infrastructure-as-code → infrastructure as code
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in value.split("\n")]
    value = "\n".join(lines)
    value = _NL_RE.sub("\n\n", value)
    return value.strip()


# ---------------------------------------------------------------- required 段落

# 英文/中文「硬性要求」标题；按最长优先参与前缀匹配。
_REQ_HEADINGS = (
    "minimum qualifications", "basic qualifications", "required qualifications", "required experience",
    "job requirements", "position requirements", "qualifications", "requirements",
    "what you'll need", "what you will need", "what you need", "what we're looking for",
    "what we are looking for", "what we need", "what we expect", "what you bring",
    "what you'll bring", "what you will bring", "you have", "you'll have", "you will have",
    "must have", "must-have", "must-haves", "must haves", "you might be a great fit",
    "what makes you a great fit", "you'll fit right in", "who you are", "your background",
    "your profile", "the ideal candidate", "skills and experience", "skills & experience",
    "experience required", "experience", "skills", "技术技能",
    "任职要求", "岗位要求", "职位要求", "工作要求", "必备条件", "任职资格", "资格要求",
    "你需要具备", "我们希望你", "任职条件", "岗位需求",
)
# 「加分/优先」标题：绝不算 required，且会结束 required 段落。
_NICE_HEADINGS = (
    "preferred qualifications", "preferred skills", "preferred experience", "desired qualifications",
    "desired skills", "desired experience", "nice to have", "nice-to-have", "nice to haves",
    "good to have", "good to haves", "bonus points", "bonus", "optional qualifications",
    "additional skills", "plus", "preferred", "desired",
    "加分项", "优先条件", "优先考虑", "加分", "优先",
)
# 其它段落标题：出现即结束 required 段落。
_OTHER_HEADINGS = (
    "responsibilities", "key responsibilities", "what you'll do", "what you will do",
    "what you'll be doing", "the role", "the opportunity", "about the role", "about the team",
    "about us", "about you", "who we are", "our mission", "our team", "the team", "benefits",
    "perks", "compensation", "salary", "location", "how to apply", "our commitment",
    "equal opportunity", "our stack", "tech stack", "technology stack", "why join",
    "why join us", "what we offer", "day to day", "day-to-day", "the company", "company",
    "岗位职责", "工作职责", "职位描述", "工作内容", "主要职责", "你将负责", "职位详情", "岗位介绍",
    "公司介绍", "关于我们", "团队介绍", "福利待遇", "福利", "薪资", "薪资范围", "工作地点",
    "我们提供", "面试流程", "招聘流程",
)

def _sorted_labels(labels):
    return sorted(set(labels), key=len, reverse=True)


_NICE_SET, _REQ_SET, _OTHER_SET = (frozenset(group) for group in (_NICE_HEADINGS, _REQ_HEADINGS, _OTHER_HEADINGS))
_NICE_PREFIXES = _sorted_labels(_NICE_HEADINGS)
_REQ_PREFIXES = _sorted_labels(_REQ_HEADINGS)


def _prefix_hit(low, labels):
    """标题前缀匹配，防止把 "Experience with Kubernetes in production" 这类正文当标题。"""
    for label in labels:
        if not low.startswith(label):
            continue
        rest = low[len(label):]
        boundary = not rest or low[len(label)] in " \t/&,，、-–—(（【《"
        if boundary and len(rest.split()) <= 3:
            return True
    return False


def _label_kind(head):
    low = re.sub(r"\s+", " ", str(head or "").strip().lower()).strip(" .。:：-–—")
    if not low:
        return None
    if low in _NICE_SET:
        return "nice"
    if low in _REQ_SET:
        return "required"
    if _prefix_hit(low, _NICE_PREFIXES):
        return "nice"
    if _prefix_hit(low, _REQ_PREFIXES):
        return "required"
    if low in _OTHER_SET:
        return "other"
    return None


_BULLET_RE = re.compile(r"^(?:[-*•·+]|\d+[.)、])\s+")


def _heading_kind(line):
    """识别标题行，返回 (kind, 冒号后的行内正文)。只认明确的标题形态，避免误伤正文。"""
    text = str(line or "").strip()
    if not text or len(text) > 300:
        return None, ""
    marked = False
    match = re.match(r"^#{1,6}[ \t]+(.*)$", text)
    if match:
        text = match.group(1).strip()
        marked = True
    bold = re.fullmatch(r"\*\*(.+?)\*\*[ \t]*[:：]?", text)
    if bold:
        text = bold.group(1).strip()
        marked = True
    text = text.strip("*_ ").strip()
    if not text or _BULLET_RE.match(text):
        return None, ""
    head, body = text, ""
    colon = re.match(r"^(.{1,100}?)[ \t]*[:：][ \t]*(.*)$", text)
    if colon:
        head, body = colon.group(1).strip(), colon.group(2).strip()
    kind = _label_kind(head)
    if kind is None and marked:
        kind = "other"  # Markdown / 加粗整行是强标题信号（如 "**Our values**"）
    if kind is None:
        return None, ""
    return kind, body


def _segments_by_lines(text):
    lines = text.split("\n")
    total = len(lines)
    segments = []
    index = 0
    while index < total:
        kind, body = _heading_kind(lines[index])
        if kind != "required":
            index += 1
            continue
        chunk = [body] if body else []
        cursor = index + 1
        while cursor < total and not lines[cursor].strip():
            cursor += 1
        while cursor < total:
            line = lines[cursor]
            if _heading_kind(line)[0] is not None:
                break
            if not line.strip():
                nxt = cursor + 1
                while nxt < total and not lines[nxt].strip():
                    nxt += 1
                if nxt >= total:
                    break
                # 空行后仍是列表项 → 视为同一段落内的排版空行
                if _BULLET_RE.match(lines[nxt]) or lines[nxt].startswith((" ", "\t")):
                    cursor = nxt
                    continue
                break
            chunk.append(line.strip())
            cursor += 1
        if chunk:
            segments.append("\n".join(chunk))
        index = cursor if cursor > index else index + 1
    return segments


def _alt(labels):
    return "|".join(re.escape(label) for label in _sorted_labels(labels))


# 扁平文本兜底（sources 层用 clean_text 把 JD 压成单行，这是线上主路径）。
# 单行里没有换行，只能靠“标题词 + 分隔符（冒号/破折号/后面紧跟大写或数字）”定位段落。
# 单行里 "experience - ..." / "the ideal candidate will ..." 这类正文很常见，
# 泛化单词不进扁平标题表；多词标题足够定位段落。
_FLAT_REQ_HEADINGS = tuple(
    label for label in _REQ_HEADINGS
    if label not in ("experience", "skills", "技术技能", "the ideal candidate")
)
# 单行里“结束段落”的标题只收明确的多词/强信号标题：
# 不能收 plus/bonus/location/company 这类单词，否则 "- 4+ years ... is a plus - ???" 会误切片段。
_FLAT_END_HEADINGS = (
    "preferred qualifications", "preferred skills", "preferred experience", "desired qualifications",
    "desired skills", "desired experience", "nice to have", "nice-to-have", "nice to haves",
    "good to have", "good to haves", "bonus points", "optional qualifications", "additional skills",
    "responsibilities", "key responsibilities", "what you'll do", "what you will do", "what you'll be doing",
    "about us", "about the team", "about the role", "benefits", "perks", "compensation", "how to apply",
    "equal opportunity", "our stack", "tech stack", "why join", "why join us", "what we offer",
    "岗位职责", "工作职责", "职位描述", "工作内容", "主要职责", "公司介绍", "关于我们", "团队介绍",
    "福利待遇", "福利", "加分项", "优先条件", "优先考虑",
)

_FLAT_SEP = r"[ \t]*(?:[:：]|[-–—•*]|[ \t]+(?=[A-Z0-9]))"
# 注意：(?i:) 只包裹标题词本身。若写成全局 (?i)，[A-Z0-9] 会匹配小写字母，
# 导致 "requirements of..." 这类正文被误判成标题。
_FLAT_REQ_RE = re.compile(r"(?i:" + _alt(_FLAT_REQ_HEADINGS) + r")" + _RB + _FLAT_SEP)
_FLAT_END_RE = re.compile(r"(?i:" + _alt(_FLAT_END_HEADINGS) + r")" + _RB + _FLAT_SEP)


def _segments_flat(text):
    """单行/扁平文本兜底：按「要求标题 → 下一个非要求标题」切片，硬上限 3000 字符。"""
    segments = []
    for match in _FLAT_REQ_RE.finditer(text):
        start = match.end()
        end_match = _FLAT_END_RE.search(text, start)
        end = end_match.start() if end_match else len(text)
        end = min(end, start + 3000, len(text))
        if end > start:
            segment = text[start:end].strip()
            if segment:
                segments.append(segment)
    return segments


def _required_segments(text):
    if not text:
        return []
    if text.count("\n") >= 2:
        segments = _segments_by_lines(text)
        if segments:
            return segments
    return _segments_flat(text)


# ---------------------------------------------------------------- 对外主函数

def _match_ids(text, entries):
    text_lower = text.lower()
    matched = []
    for skill, pattern, triggers in entries:
        if triggers is not None and not any(trigger in text_lower for trigger in triggers):
            continue
        if pattern.search(text):
            matched.append(skill["id"])
    return matched


def extract(text, taxonomy=None):
    """从 JD 文本抽取技能。

    返回 {"skills": [id...], "required": [id...], "counts": {id: 次数}, "by_category": {category: [id...]}}
    - skills/required/by_category 均按 taxonomy 顺序，counts 的 key 顺序同 taxonomy。
    - required 是 skills 的子集；找不到要求段落时为 []。
    """
    taxonomy = taxonomy if taxonomy is not None else _default_taxonomy()
    entries, _by_id = _compiled_for(taxonomy)
    document = _prepare_text(text)
    document_lower = document.lower()

    counts = {}
    for skill, pattern, triggers in entries:
        if triggers is not None and not any(trigger in document_lower for trigger in triggers):
            continue
        hits = sum(1 for _ in pattern.finditer(document))
        if hits:
            counts[skill["id"]] = hits

    matched_ids = set(counts)
    required_ids = set()
    if document:
        for segment in _required_segments(document):
            for sid in _match_ids(segment, entries):
                if sid in matched_ids:
                    required_ids.add(sid)

    ordered_skills = [skill["id"] for skill in taxonomy["skills"] if skill["id"] in matched_ids]
    ordered_required = [skill["id"] for skill in taxonomy["skills"] if skill["id"] in required_ids]
    by_category = {}
    for skill in taxonomy["skills"]:
        if skill["id"] in matched_ids:
            by_category.setdefault(skill["category"], []).append(skill["id"])

    return {
        "skills": ordered_skills,
        "required": ordered_required,
        "counts": counts,
        "by_category": by_category,
    }
