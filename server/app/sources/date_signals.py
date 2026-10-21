"""从页面里找「发布日期 / 成文日期」的信号（纯函数，不联网、不读库）。

为什么要单独一层：政务页的日期**不一定**写成「发布日期：2026-09-01」。它可能在
`<meta>` / JSON-LD / `<time datetime>` 里，也可能只是正文末尾落款处的一行日期。
但两类日期的语义不同，不能互相顶替：

- `publish_date`（发布日期）只认：检索/列表日期、页面标签（发布日期/发布时间/
  常见的「日期：… 来源：…」页头）、页面自带的发布时间元数据；
- `signed_date`（成文日期）只认：标签（成文/印发/发文日期）、正文末尾的落款日期。

本模块只负责「从 HTML / 正文里找候选」，落库与展示由调用方决定。防误判的口径：
日期必须完整（年月日）、落在 1990 年至今、落款只扫正文末尾，且排除
「自……起施行」「有效期」「版权/备案」等噪声。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.sources.htmlparse import Node, parse_html, select
from app.sources.util import find_dates, parse_date

# 发布日期不可能早于这个年份，也不可能是未来；区间外的候选一律当没找到。
_MIN_YEAR = 1990

# 页面里常见的发布时间元数据键（统一小写后比较）。
_PUBLISH_META_KEYS = {
    "article:published_time",
    "og:published_time",
    "datepublished",
    "datecreated",
    "pubdate",
    "publishdate",
    "publish_date",
    "publish-date",
    "dc.date",
    "dc.date.issued",
    "dcterms.date",
    "sailthru.date",
    "parsely-pub-date",
    "weibo:article:create_at",
}

# `<time datetime>` 只有在 class/id 带这些提示时才算发布时间；正文里的事件日期
# 也常用 `<time>`，不加这个门会误取。
_TIME_HINT_RE = re.compile(r"date|time|pub|publish|meta|info|source|article", re.I)

# 可见的发布日期标签。政务站页头常见「日期： 2026-09-23 11:09 来源：…」或
# 「发布日期：2026-09-23」；只认带冒号的标签，避免正文句子里的「发布日期为…」。
_DATE_CORE = (
    r"((?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?)"
)
_PUBLISH_LABEL_RE = re.compile(
    rf"(?:发布日期|发布时间|发布于|发表于)\s*[:：]\s*{_DATE_CORE}"
)
# 「日期：… 来源：…」是页头最常见的发布日期形态；前面不能是成文/印发/发文等
# 语义标签（否则会把成文日期当发布日期）。可带时分，也可以只有日期。
_SITE_DATE_LABEL_RE = re.compile(
    rf"(?<!成文)(?<!印发)(?<!发文)(?<!签发)(?<!落款)(?<!施行)(?<!生效)(?<!截止)"
    rf"(?<!生成)(?<!维护)"
    rf"日\s*期\s*[:：]\s*{_DATE_CORE}"
    rf"(?:\s+\d{{1,2}}\s*[:：]\s*\d{{2}})?\s*.{{0,30}}?来\s*源"
)

# 页脚/导航/工具条：落款扫描时整行跳过。
_NOISE_RE = re.compile(
    r"版权|备案|ICP|网站标识|主办单位|技术支持|访问量|浏览量|扫一扫|二维码|"
    r"来源|信息来源|责任编辑|上一篇|下一篇|相关阅读|相关推荐|打印本页|关闭窗口|返回顶部"
)

# 落款行里出现这些词说明日期属于别的语义（施行/有效期/发布时间等），不能当落款。
_REJECT_RE = re.compile(
    r"施行|生效|有效期|截止|报名|公示期|起始|结束|发布|版权|备案|ICP|网站标识|"
    r"来源|信息来源|责任编辑|主办单位|技术支持|访问量|浏览量|扫一扫|二维码|"
    r"上一篇|下一篇|相关阅读|相关推荐|打印本页|关闭窗口|返回顶部"
)

_LABEL_RE = re.compile(r"^(成文日期|印发日期|发文日期|签发日期|落款日期)\s*[:：]?$")

# 机关名常见结尾。只用于「日期独占一行」时判断上一行是不是落款机关，
# 不做全文机关识别（那件事由字段规则/模型负责）。
_AGENCY_SUFFIXES = (
    "政府",
    "委员会",
    "办公室",
    "办公厅",
    "管委会",
    "委",
    "办",
    "指挥部",
    "领导小组",
    "党委",
    "党组",
    "人大",
    "政协",
    "法院",
    "检察院",
    "分局",
    "街道办",
    "镇政府",
    "局",
    "厅",
    "部",
    "院",
    "署",
    "公司",
    "中心",
    "总队",
    "支队",
    "大队",
    "行",
    "社",
    "台",
    "校",
    "所",
)


@dataclass(frozen=True)
class DateHit:
    """一次命中的日期、原文片段与信号来源（`jsonld` / `meta` / `time` / `signature`）。"""

    value: date
    text: str
    source: str


def _safe_date(value: object) -> date | None:
    """`parse_date` 之后再卡合理区间：未来日期与 1990 年前的“日期”基本都是误读。"""
    parsed = parse_date(value)
    if parsed is None or parsed.year < _MIN_YEAR or parsed > date.today():
        return None
    return parsed


def _script_text(node: Node) -> str:
    """取 `<script>` 的原始文本（`text_content()` 会跳过 script/style）。"""
    return " ".join(child.text for child in node.children if child.tag == "#text")


def publish_date_from_meta(html: str) -> DateHit | None:
    """从页面元数据里找发布日期：JSON-LD → `<meta>` → 带提示的 `<time>`。

    这层只认**明确声明为发布时间**的信号；页面正文里的日期不在这里处理，避免把
    「自 2026 年起施行」之类的正文日期误当发布日期。
    """
    if not html:
        return None
    try:
        root = parse_html(html)
    except Exception:  # noqa: BLE001 - 解析失败只表示这层拿不到，不能拖垮抓取
        return None

    # 1. JSON-LD 的 datePublished / dateCreated（CMS 结构化数据，置信度最高）
    for node in select(root, "script"):
        if "ld+json" not in node.get("type").lower():
            continue
        text = _script_text(node)
        if not text:
            continue
        for key in ("datePublished", "dateCreated"):
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text, re.I)
            if not match:
                continue
            parsed = _safe_date(match.group(1))
            if parsed is not None:
                return DateHit(parsed, match.group(1).strip(), "jsonld")

    # 2. 常见发布时间 meta 标签
    for node in select(root, "meta"):
        key = (
            node.get("property") or node.get("name") or node.get("itemprop") or ""
        ).strip().lower()
        if key not in _PUBLISH_META_KEYS:
            continue
        raw = node.get("content")
        parsed = _safe_date(raw)
        if parsed is not None:
            return DateHit(parsed, raw.strip(), "meta")

    # 3. `<time datetime>`：只接受 class/id 带 date/time/pub 提示的（正文事件日期也用它）
    for node in select(root, "time"):
        parent = node.parent
        hint = " ".join(
            [
                node.get("class"),
                node.get("id"),
                parent.get("class") if parent is not None else "",
                parent.get("id") if parent is not None else "",
            ]
        )
        if not _TIME_HINT_RE.search(hint):
            continue
        raw = node.get("datetime") or node.text_content()
        parsed = _safe_date(raw)
        if parsed is not None:
            return DateHit(parsed, raw.strip(), "time")

    return None


def publish_date_from_label_text(text: str) -> DateHit | None:
    """从**可见文本**的页头标签里找发布日期（`日期：…来源：…` / `发布日期：…`）。

    只扫前 6000 字：页头/标题区在这段内，后面的正文、相关推荐和页脚不参与，
    避免把正文里的「日期」或页脚模板日期当成发布日期。
    """
    flat = " ".join((text or "").split())[:6000]
    if not flat:
        return None
    # 先认「日期 + 来源」的页头形态，再认明确的发布标签；两者都要求带冒号。
    for pattern in (_SITE_DATE_LABEL_RE, _PUBLISH_LABEL_RE):
        for match in pattern.finditer(flat):
            parsed = _safe_date(match.group(1))
            if parsed is not None:
                return DateHit(parsed, match.group(1).strip(), "label")
    return None


def publish_date_from_label(root: Node) -> DateHit | None:
    """从已解析的页面 DOM 取可见发布日期标签；`parse_html` 只做一次。"""
    return publish_date_from_label_text(root.text_content())


def _looks_like_date_label(text: str) -> bool:
    return bool(_LABEL_RE.match(text.strip(" \t　")))


def _looks_like_tail_note(text: str) -> bool:
    """落款日期后面常见的短说明（「（此件公开发布）」），不是正文段落。"""
    compact = text.strip()
    if not compact or len(compact) > 30:
        return False
    if re.match(r"^[（(].*[）)]$", compact):
        return True
    return any(word in compact for word in ("此件", "公开发布", "依申请公开"))


def _looks_like_agency(text: str) -> bool:
    """落款机关行：短、无日期、无页脚噪声，且以常见机关后缀结尾。"""
    compact = text.strip(" \t　：:，,。.;；")
    if not 2 <= len(compact) <= 50:
        return False
    if find_dates(compact) or _NOISE_RE.search(compact):
        return False
    if "人民政府" in compact:
        return True
    if compact.startswith("中共") and compact.endswith(("委员会", "党组")):
        return True
    return compact.endswith(_AGENCY_SUFFIXES)


def signature_date(body: str) -> DateHit | None:
    """从正文末尾找落款/成文日期（机关名下面的那一行日期）。

    只看最后 6 个非噪声行，宁缺毋滥：日期独占一行（最后一行、上一行是机关名，
    或下一行是「（此件公开发布）」这类短说明），或「机关名 + 日期」写在同一行。
    正文中段的日期、施行日期、页脚版权日期都不算。
    """
    lines = [" ".join(line.split()) for line in (body or "").splitlines()]
    lines = [line for line in lines if line and len(line) <= 160]
    if not lines:
        return None

    # 先把页脚/导航行剔掉，避免「最后一行是备案信息」把真正的落款挤出扫描窗口。
    content = [line for line in lines if not _NOISE_RE.search(line)]
    tail = content[-6:]
    if not tail:
        return None
    last_index = len(tail) - 1

    for index in range(last_index, -1, -1):
        line = tail[index]
        if _REJECT_RE.search(line):
            continue
        for parsed, start, end in reversed(find_dates(line)):
            if parsed.year < _MIN_YEAR or parsed > date.today():
                continue
            text = line[start:end]
            before = line[:start].strip(" \t　（(【[")
            after = line[end:].strip(" \t　）)】]。.,，;；")

            # 日期后面还跟着正文 → 不是落款行（「2026年9月1日至9月5日」等）。
            if after not in ("", "印发", "印发日期"):
                continue

            if before:
                # 「机关名 2026年9月1日」或「成文日期：2026年9月1日」。
                if not (_looks_like_agency(before) or _looks_like_date_label(before)):
                    continue
            elif index > 0 and _REJECT_RE.search(tail[index - 1]):
                # 日期独占一行，但上一行是「公示期 / 有效期至 / 发布时间」等，
                # 这个日期属于别的语义，不能当落款。
                continue
            elif index != last_index:
                # 日期独占一行但不在最后：上一行是机关落款，或下一行是
                # 「（此件公开发布）」这类短说明时才认。index=0 时没有上一行。
                previous_agency = index > 0 and _looks_like_agency(tail[index - 1])
                next_note = index < last_index and _looks_like_tail_note(tail[index + 1])
                if not (previous_agency or next_note):
                    continue

            return DateHit(parsed, text, "signature")

    return None
