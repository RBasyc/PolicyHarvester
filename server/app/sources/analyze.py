"""页面结构分析：从一页 HTML 里**猜出**配置所需的候选（纯函数，离线可测）。

这是接入流程「阶段 2 勘察 / 阶段 3 样本分析」的算力所在：给定一个入口页或详情页，
给出可用的列表容器 / 正文容器 / 元信息 / 翻页规则 / 附件 / 订阅源 / 检索入口候选。

**这些结果是候选，不是结论**——本模块只做启发式打分，命中与否由后续「样本获取」
与「试运行验收」用真实页面验证。凡未验证的候选，报告里一律写成「候选（待试运行确认）」，
不允许直接当成已确认的接入配置（对应 Skill 的「未验证内容不得标记为已确认」）。

打分口径（都写得比较保守，宁可多给几个候选让人/模型复核）：

- 列表容器：同父节点下**同签名兄弟 ≥ 3 个**、每个都带可达链接、平均文本 ≥ 8 字
  （导航菜单的链接文本普遍很短，这条把它们滤掉）；
- 正文容器：文本量为主、常见 id/class（zoom / UCAP-CONTENT / TRS_Editor / article…）
  加权；链接密度过高（链接文本占正文过半）的容器降权（那是导航或列表）；
- 元信息：只在**短文本叶子节点**上匹配日期 / 文号 / 「来源：」标签，避免把整段正文
  当成日期；
- 翻页：从「下一页」链接与页码链接的数字反推参数名或路径模板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from app.sources.htmlparse import (
    SKIP_TAGS,
    Node,
    describe_selector,
    parse_html,
    select,
    select_one,
    strip_nth_of_type,
)

# ---------- 正则 ----------

_DATE_RE = re.compile(r"(19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}")
_DOCNO_RE = re.compile(r"[〔\[（(]\s*(19|20)\d{2}\s*[〕\]）)]\s*第?\s*\d+\s*号")
_AGENCY_LABEL_RE = re.compile(r"^\s*(来源|信息来源|发布机构|发布单位|发文机关|制定机关)\s*[:：]\s*(.+)$")
_NEXT_PAGE_TEXTS = ("下一页", "下页", "后一页", "末页", "尾页", "下一頁")
_PAGE_NUM_RE = re.compile(r"^\s*\[?\s*(\d{1,3})\s*\]?\s*$")
_INDEX_PATH_RE = re.compile(r"^(.*?)(\d+)(\.[a-zA-Z]+)$")

# 常见正文容器名（命中即加权：政务站模板高度雷同，这张表覆盖了绝大多数国产 CMS）
_CONTAINER_IDS = {
    "zoom", "content", "ucap-content", "article", "trs_editor", "content_body",
    "divcontent", "detail", "maintext", "fontzoom", "zoomcon", "contentbox",
    "xxgk_content", "wzcontent", "zwnr", "nr", "content_area",
}
_CONTAINER_CLASSES = {
    "article", "article-content", "content", "trs_editor", "view", "detail",
    "detail-content", "zoom", "neirong", "conbox", "xxgk-content", "pages_content",
    "zwnr", "content-box", "article_content", "txt", "text", "main-content",
}

# 栏目入口链接的文本特征（用于从门户首页找内容栏目）
_COLUMN_HINTS = (
    "政策", "文件", "通知", "公告", "规定", "规章", "规范性文件", "信息公开",
    "政务公开", "法律法规", "政策法规", "政策文件", "公示", "意见", "办法", "解读",
)
# 明显不是内容栏目的链接（导航/工具）
_COLUMN_DENY = ("登录", "注册", "english", "无障碍", "网站地图", "sitemap", "rss", "邮箱", "微信", "微博")


# ---------- 数据结构 ----------


@dataclass
class Candidate:
    """一个候选及它的证据（证据要一起给出去，模型才能判断该不该采纳）。"""

    kind: str  # list / detail / date / doc_no / agency / title / next_page / column
    selector: str | None
    score: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "score": round(self.score, 1)}
        if self.selector:
            out["selector"] = self.selector
        out.update(self.evidence)
        return out


# ---------- 列表容器 ----------


def _link_of(node: Node) -> Node | None:
    """取条目里的可达链接节点（条目本身是 <a> 时就是它自己）。"""
    if node.tag == "a" and (node.get("href") or "").strip():
        return node
    return select_one(node, "a[href]")


def _usable_href(node: Node) -> str | None:
    href = (node.get("href") or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#", "tel:")):
        return None
    return href


def list_candidates(
    root: Node, *, min_items: int = 3, limit: int = 6, base_url: str = ""
) -> list[Candidate]:
    """找「一组长得一样、每个都带链接」的兄弟结构 = 列表条目。"""
    found: dict[str, Candidate] = {}
    parents = [root, *root.iter_elements()]
    for parent in parents:
        groups: dict[str, list[Node]] = {}
        for child in parent.children:
            if child.tag == "#text" or child.tag in SKIP_TAGS:
                continue
            signature = child.tag + ("." + sorted(child.classes)[0] if child.classes else "")
            groups.setdefault(signature, []).append(child)

        for signature, items in groups.items():
            if len(items) < min_items:
                continue
            links = [(item, _link_of(item)) for item in items]
            links = [(item, link) for item, link in links if link is not None and _usable_href(link)]
            if len(links) < min_items:
                continue
            texts = [item.text_content().strip() for item in items]
            avg_text = sum(len(t) for t in texts) / max(1, len(texts))
            if avg_text < 8:  # 导航菜单的链接文本几乎都很短
                continue

            parent_selector = describe_selector(parent)
            stripped = strip_nth_of_type(parent_selector)
            selector = f"{stripped} > {signature}"
            if len(selector) > 120:
                continue
            item, link = links[0]
            evidence = {
                "item_count": len(items),
                "avg_text_len": int(avg_text),
                "sample_title": texts[0][:60],
                "sample_url": urljoin(base_url, _usable_href(link) or ""),
                "nth_stripped": stripped != parent_selector,
            }
            score = min(len(items), 60) * 1.0 + min(avg_text, 60) / 6
            if parent.get("id") or parent.classes:
                score += 8
            candidate = Candidate(kind="list", selector=selector, score=score, evidence=evidence)
            previous = found.get(selector)
            if previous is None or candidate.score > previous.score:
                found[selector] = candidate

    return sorted(found.values(), key=lambda c: -c.score)[:limit]


# ---------- 正文容器 ----------


def detail_candidates(root: Node, *, limit: int = 6, min_chars: int = 80) -> list[Candidate]:
    """找正文容器：文本量大、链接密度低、名字像正文的那些节点。"""
    page_text_len = max(1, len(root.text_content()))
    out: list[Candidate] = []
    for node in root.iter_elements():
        if node.tag in SKIP_TAGS or node.tag in ("html", "body", "a", "li", "td", "th", "tr", "ul", "ol", "nav", "header", "footer", "form", "select"):
            continue
        text = node.text_content()
        text_len = len(text.strip())
        if text_len < min_chars:
            continue
        link_text = sum(len(link.text_content()) for link in select(node, "a"))
        link_ratio = link_text / max(1, text_len)
        if link_ratio > 0.5:  # 链接文本占一半以上 = 导航/列表，不是正文
            continue
        node_id = (node.get("id") or "").lower()
        classes = {c.lower() for c in node.classes}
        score = min(text_len, 4000) / 200.0
        if node_id in _CONTAINER_IDS:
            score += 60
        if classes & _CONTAINER_CLASSES:
            score += 35
        if node.tag in ("article", "main"):
            score += 20
        score -= link_ratio * 20
        selector = describe_selector(node)
        if strip_nth_of_type(selector) != selector:
            score -= 15  # 需要 nth-of-type 才定位得准 → 不稳，降权但保留
        out.append(
            Candidate(
                kind="detail",
                selector=selector,
                score=score,
                evidence={
                    "text_len": text_len,
                    "text_ratio": round(text_len / page_text_len, 2),
                    "link_ratio": round(link_ratio, 2),
                    "paragraphs": len(select(node, "p")) + len(select(node, "br")),
                    "tag": node.tag,
                    "sample": text.strip()[:80],
                },
            )
        )
    out.sort(key=lambda c: -c.score)
    return out[:limit]


# ---------- 元信息 ----------


def _own_text(node: Node) -> str:
    """只取节点**直接**的文本（不含子元素），用于短字段定位。"""
    return " ".join(child.text.strip() for child in node.children if child.tag == "#text").strip()


def meta_candidates(root: Node, *, limit: int = 4) -> dict[str, list[Candidate]]:
    """定位标题 / 日期 / 文号 / 来源机构：只在短文本节点上匹配（避免把正文当成日期）。"""
    dates: list[Candidate] = []
    docnos: list[Candidate] = []
    agencies: list[Candidate] = []
    titles: list[Candidate] = []

    for node in root.iter_elements():
        if node.tag in SKIP_TAGS:
            continue
        own = _own_text(node)
        if not own and node.tag in ("h1", "h2", "h3"):
            own = node.text_content().strip()[:120]
        if not own or len(own) > 80:
            continue
        selector = describe_selector(node)

        match = _DATE_RE.search(own)
        if match:
            dates.append(
                Candidate("date", selector, evidence={"sample": own[:40], "matched": match.group(0)})
            )
        if _DOCNO_RE.search(own):
            docnos.append(Candidate("doc_no", selector, evidence={"sample": own[:60]}))
        label = _AGENCY_LABEL_RE.match(own)
        if label:
            agencies.append(
                Candidate("agency", selector, evidence={"sample": own[:60], "label": label.group(1)})
            )

    # 标题：h1/h2 优先，其次 id/class 含 title 的节点，最后 <title>
    for selector in ("h1", "h2", "div.title", "span.title", "h1.title", "td.title"):
        for node in select(root, selector):
            text = node.text_content().strip()
            if 4 <= len(text) <= 120:
                titles.append(Candidate("title", selector, score=len(text), evidence={"sample": text}))
                break
    head = select_one(root, "title")
    if head is not None:
        titles.append(
            Candidate("title", "title", score=1, evidence={"sample": head.text_content().strip()[:120]})
        )

    return {
        "title": titles[:limit],
        "date": dates[:limit],
        "doc_no": docnos[:limit],
        "agency": agencies[:limit],
    }


# ---------- 翻页 ----------


def _pagination_from_links(anchors: list[Node], base_url: str) -> dict[str, Any]:
    """从页码链接反推翻页规则：参数名 / 路径模板 / 观察到的页码值。

    `start` 是**第 1 页的取值**，由观察值反推（分页条通常不给出「第 1 页」的链接）：
    看到 `[2,3,4]` 说明当前就在第 1 页、从 2 开始列 → `start = 2-1 = 1`；
    看到 `[1,2,3]` 说明分页条含第 1 页 → `start = 1`；看到 `[0,1,2]` → `start = 0`。
    这条推断写进 `start_reason` 一并返回，配置里可随时改。
    """
    numeric: list[tuple[int, str]] = []
    for anchor in anchors:
        href = urljoin(base_url, _usable_href(anchor) or "")
        text_match = _PAGE_NUM_RE.match(anchor.text_content())
        if not text_match or not href:
            continue
        numeric.append((int(text_match.group(1)), href))
    if len(numeric) < 2:
        return {}

    values = sorted({value for value, _ in numeric})
    first_value, first_href = numeric[0]
    parts = urlsplit(first_href)

    if 0 in values:
        start, reason = 0, "观察到的页码含 0 → 页码从 0 起"
    elif 1 in values:
        start, reason = 1, "观察到的页码含 1 → 页码从 1 起"
    else:
        start, reason = max(1, min(values) - 1), f"最小观察值 {min(values)}，按「当前页=第 1 页」反推"

    # 情形一：页码在查询参数里（href 中某个参数值正好等于该页码）
    for name, items in parse_qs(parts.query).items():
        if items and items[0] == str(first_value):
            return {
                "kind": "query_param",
                "param": name,
                "start": start,
                "start_reason": reason,
                "observed_values": values,
                "example": first_href,
            }

    # 情形二：页码在路径里（index_2.html）
    path_match = _INDEX_PATH_RE.match(parts.path)
    if path_match and int(path_match.group(2)) == first_value:
        template = f"{path_match.group(1)}{{page}}{path_match.group(3)}"
        return {
            "kind": "path_index",
            "param": "page",
            "path_template": template,
            "start": start,
            "start_reason": reason,
            "observed_values": values,
            "example": first_href,
        }

    # 页码链接认出来了，但反推不出规则 —— 别猜，交给人工/模型看
    return {
        "kind": "unknown",
        "observed_values": values,
        "example": first_href,
        "note": "无法反推翻页参数，请人工确认分页地址规律",
    }


def pagination_candidates(root: Node, base_url: str) -> dict[str, Any]:
    """翻页候选：优先给「下一页」链接（最可靠），再给页码反推的规则。"""
    next_href = None
    numeric_anchors: list[Node] = []
    for anchor in select(root, "a[href]"):
        text = anchor.text_content().strip()
        href = _usable_href(anchor)
        if not href:
            continue
        if not next_href and text in _NEXT_PAGE_TEXTS:
            next_href = urljoin(base_url, href)
        if _PAGE_NUM_RE.match(text):
            numeric_anchors.append(anchor)

    rule = _pagination_from_links(numeric_anchors, base_url)
    out: dict[str, Any] = {"page_links": len(numeric_anchors)}
    if next_href:
        out["next_href"] = next_href
    out.update(rule)
    return out


# ---------- 入口/订阅/检索 ----------


def column_links(root: Node, base_url: str, *, limit: int = 20) -> list[dict[str, str]]:
    """首页/栏目页里像「内容栏目」的链接（政策、文件、通知、信息公开…）。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in select(root, "a[href]"):
        text = " ".join(anchor.text_content().split())
        href = _usable_href(anchor)
        if not href or not text or len(text) > 40:
            continue
        lowered = text.lower()
        url = urljoin(base_url, href)
        if url in seen or any(deny in lowered or deny in url.lower() for deny in _COLUMN_DENY):
            continue
        if not any(hint in text for hint in _COLUMN_HINTS):
            continue
        seen.add(url)
        out.append({"text": text, "url": url})
        if len(out) >= limit:
            break
    return out


def feeds_and_sitemaps(root: Node, base_url: str) -> dict[str, list[str]]:
    """站点自带的机器可读入口：RSS/Atom 订阅与 sitemap（有的话比抓 HTML 稳得多）。"""
    feeds: list[str] = []
    sitemaps: list[str] = []
    for node in select(root, 'link[rel="alternate"]'):
        node_type = (node.get("type") or "").lower()
        if "rss" in node_type or "atom" in node_type:
            href = _usable_href(node)
            if href:
                feeds.append(urljoin(base_url, href))
    for anchor in select(root, "a[href]"):
        href = (_usable_href(anchor) or "").lower()
        absolute = urljoin(base_url, _usable_href(anchor) or "")
        if not absolute:
            continue
        if any(token in href for token in ("rss", "atom", ".xml", "/feed")):
            feeds.append(absolute)
        if "sitemap" in href:
            sitemaps.append(absolute)
    return {"feeds": sorted(set(feeds))[:5], "sitemaps": sorted(set(sitemaps))[:5]}


def search_forms(root: Node, base_url: str) -> list[dict[str, str]]:
    """站内检索入口候选（有的话可以走检索而不是翻栏目，覆盖更全）。"""
    out: list[dict[str, str]] = []
    for form in select(root, "form"):
        action = (form.get("action") or "").strip()
        for field_node in select(form, "input"):
            name = (field_node.get("name") or "").strip()
            input_type = (field_node.get("type") or "text").lower()
            if input_type in ("text", "search") and name:
                row = {"action": urljoin(base_url, action) if action else base_url, "field": name}
                if row not in out:
                    out.append(row)
    return out[:5]


# ---------- 汇总 ----------


def analyze_page(html: str, url: str, *, listed_as: str = "page") -> dict[str, Any]:
    """一页 HTML → 结构分析报告（`listed_as` 只影响输出里的自我标注）。

    返回的每一条都是**候选**：`evidence` 是关键指标，采纳与否由试运行验证。
    """
    root = parse_html(html)
    meta = meta_candidates(root)
    return {
        "listed_as": listed_as,
        "page_title": (select_one(root, "title").text_content().strip() if select_one(root, "title") else "")[:120],
        "text_len": len(root.text_content()),
        "list_candidates": [c.as_dict() for c in list_candidates(root, base_url=url)],
        "detail_candidates": [c.as_dict() for c in detail_candidates(root)],
        "meta_candidates": {name: [c.as_dict() for c in items] for name, items in meta.items()},
        "pagination": pagination_candidates(root, url),
        "columns": column_links(root, url),
        "feeds": feeds_and_sitemaps(root, url),
        "search_forms": search_forms(root, url),
        "attachments": sorted(
            {urljoin(url, _usable_href(a) or "") for a in select(root, "a[href]") if _is_attachment(a)}
        )[:20],
    }


_ATTACHMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".ofd", ".wps")


def _is_attachment(anchor: Node) -> bool:
    href = (_usable_href(anchor) or "").lower()
    return href.endswith(_ATTACHMENT_SUFFIXES)
