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
- 翻页：从「下一页」链接与页码链接的数字反推参数名或路径模板；
- 命名区域（`region_candidates`）：`role=tablist` 的 tab 与 `role=tabpanel` 按文档序
  一一配对（数量不等就不配对），区域名取 tab 的 `wzades`/`aria-label`/链接文本；普通
  列表组向上三层找标题型文本（h1-h4 / class 含 tit/head/hd）作为模块名。两者都给出
  `scope_selector` + `scope_index`，供「同页多个同签名列表只取其一」的入口配置使用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from app.sources.field_rules import normalize_label, strip_leading_label
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

# 标签 → 标准字段名。字段规则的 auto-generation 靠它把「页面上写的标签」映射到
# `detail.fields` 的保留名（title/agency/doc_no/date…）或 metadata 键。
# 认不出的标签仍会作为 `unknown` 候选输出（label 原样保留），由模型决定怎么用。
_FIELD_LABELS = {
    "索引号": "index_no",
    "文号": "doc_no",
    "发文字号": "doc_no",
    "公文文号": "doc_no",
    "文件编号": "doc_no",
    "发文机关": "agency",
    "发布机构": "agency",
    "发布单位": "agency",
    "制定机关": "agency",
    "责任部门": "agency",
    "信息来源": "source",
    "来源": "source",
    "发布日期": "publish_date",
    # 「发文日期」指文件签发/印发日期，不是网站发布日期；发布日期只认
    # 「发布日期 / 发布时间」。否则它会顶替 publish_date（2026-10-21 裁定）。
    "发文日期": "signed_date",
    "发布时间": "publish_date",
    "成文日期": "signed_date",
    "印发日期": "signed_date",
    "签发日期": "signed_date",
    "落款日期": "signed_date",
    "主题分类": "theme",
    "主题词": "theme",
    "分类": "theme",
    "公文种类": "doc_type",
    "文种": "doc_type",
    "施行日期": "effective_date",
    "生效日期": "effective_date",
    "关键词": "keywords",
    "作者": "author",
    "有效性": "validity",
    "效力状态": "validity",
}
# 标签本身必须短：长文本里恰好有冒号（正文句子）不能被当成「标签：值」
_FIELD_LABEL_MAX = 12
_FIELD_VALUE_MAX = 160
# 认不得的标签（站点自定义字段）额外要求值足够短——正文里「工作要求：……」这类
# 句子标签对不上标准字段名，长度是最后一道区分。
_FIELD_UNKNOWN_VALUE_MAX = 40
# 这些「标签」是交互文字不是字段：`<span>更多：</span>` 之类不能进字段候选
_FIELD_LABEL_DENY = {
    "更多", "详情", "点击", "查看", "下载", "打印", "关闭", "返回", "首页",
    "上一篇", "下一篇", "相关", "附件",
}
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

# 「这个输入框是关键词框吧」的名字特征（表单不在 form 里时，只报事实、不猜提交地址）
_SEARCH_FIELD_HINTS = ("keyword", "searchword", "search", "q", "query", "wd", "key", "keywordvalue")


# ---------- 数据结构 ----------


@dataclass
class Candidate:
    """一个候选及它的证据（证据要一起给出去，模型才能判断该不该采纳）。"""

    kind: str  # list / detail / date / doc_no / agency / title / next_page / column
    selector: str | None
    score: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    # 命中的节点本身，仅供同类调用方复用（如 `html_source` 的正文兜底）——
    # 选择器引擎不支持伪类，而 `describe_selector` 可能生成 `:nth-of-type`，
    # 拿选择器回头再查可能落空，所以把节点一并带出来。**不进 `as_dict`**：
    # 报告是给模型/人看的 JSON，不该塞进整棵子树。
    node: Node | None = field(default=None, repr=False, compare=False)

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


def _content_groups(
    root: Node, *, min_items: int = 3
) -> list[tuple[Node, str, list[Node], list[tuple[Node, Node]], float]]:
    """同父节点下「同签名兄弟」聚组，并滤掉导航/页脚的形状。

    返回 `(parent, signature, items, links, avg_text)`：`links` 是「条目 → 条目内可达链接」
    对（每条都带可用 href 才入选）。`list_candidates` 与 `region_candidates` 共用这道
    分组——两边的「什么算列表」口径必须一致，否则分析报告和区域枚举会各说各话。
    """
    groups: list[tuple[Node, str, list[Node], list[tuple[Node, Node]], float]] = []
    parents = [root, *root.iter_elements()]
    for parent in parents:
        by_signature: dict[str, list[Node]] = {}
        for child in parent.children:
            if child.tag == "#text" or child.tag in SKIP_TAGS:
                continue
            signature = child.tag + ("." + sorted(child.classes)[0] if child.classes else "")
            by_signature.setdefault(signature, []).append(child)

        for signature, items in by_signature.items():
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
            groups.append((parent, signature, items, links, avg_text))
    return groups


def _child_signature(node: Node) -> str:
    """与分组同口径的单个节点签名（区域 list_selector 用）。"""
    return node.tag + ("." + sorted(node.classes)[0] if node.classes else "")


def list_candidates(
    root: Node, *, min_items: int = 3, limit: int = 6, base_url: str = ""
) -> list[Candidate]:
    """找「一组长得一样、每个都带链接」的兄弟结构 = 列表条目。"""
    found: dict[str, Candidate] = {}
    for parent, signature, items, links, avg_text in _content_groups(root, min_items=min_items):
        parent_selector = describe_selector(parent)
        stripped = strip_nth_of_type(parent_selector)
        selector = f"{stripped} > {signature}"
        if len(selector) > 120:
            continue
        item, link = links[0]
        texts = [item.text_content().strip() for item in items]
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


# ---------- 命名内容区域 ----------

# 标签页 tab 名字的取值顺序：wysiwyg 标注属性（gz.gov.cn 系的 wzades 正好是短栏目名）→
# aria-label（通常是一整句无障碍描述，太长）→ 链接文本。都是「实测页面上有的」，不猜。
_TAB_NAME_ATTRS = ("wzades", "aria-label", "title")
# 模块栏目标签的 class 特征（政务 CMS 模板高度雷同：common_tit / tit / hd / head…）
_HEADING_CLASS_HINTS = ("tit", "head", "hd")
# 这些 class/标签是**页面骨架**不是栏目标题——div.header（站头导航）的 class 含 "head"，
# 会把「首页 政务公开 部门动态」这种导航串当成栏目名（2026-10-16 回归用例抓到的误报）。
_LABEL_DENY_CLASSES = ("header", "headr", "nav", "menu", "footer", "foot", "crumb", "logo", "search", "topbar")
_LABEL_DENY_TAGS = ("header", "nav", "footer", "form")
_HEADING_TAGS = ("h1", "h2", "h3", "h4")


def _looks_like_chrome(node: Node) -> bool:
    """节点是不是页面骨架（站头/导航/页脚）——骨架的短文本是导航串，不是栏目名。"""
    if node.tag in _LABEL_DENY_TAGS:
        return True
    classes = " ".join(node.classes).lower()
    return any(deny in classes for deny in _LABEL_DENY_CLASSES)


def _short_label(text: str, *, max_len: int = 20) -> str:
    """收一句干净短标签：压空白、去尾部冒号，长度落在 2..max_len 才算数。"""
    cleaned = " ".join((text or "").split()).strip(" ：:")
    return cleaned if 2 <= len(cleaned) <= max_len else ""


def _tab_label(tab: Node) -> str:
    """标签页 tab 的名字：优先取节点属性里的短标注，兜底链接文本。"""
    for attr in _TAB_NAME_ATTRS:
        label = _short_label(tab.get(attr) or "")
        if label:
            return label
    link = _link_of(tab)
    if link is not None:
        return _short_label(link.text_content())
    return ""


def _is_nav_bar(node: Node) -> bool:
    """标题候选里塞着 ≥2 个内容链接 = 它是导航条，不是标题。

    政务模板常在模块头放一整条栏目快捷导航 + 多个「更多>>」，text_content 会拼成
    「政策文件 政策解读 规划信息 人事任免 更多>>…」这种串——2026-10-16 实测站点
    五个模块共用同一条导航头，五个区域全被命名成这串东西，筛选面板上没法看。
    """
    meaningful = 0
    for a in node.iter_elements():
        if a.tag != "a":
            continue
        text = _short_label(a.text_content())
        if text and "更多" not in text and "more" not in text.lower():
            meaningful += 1
        if meaningful >= 2:
            return True
    return False


# 「更多>>」类噪声链接的文本片段，命中的都从标题里剥掉（真标题含「更多」的极少）。
_MORE_NOISE = ("更多>>", "更多>", "更多", "More>>", "more>>", "More", "more")


def _heading_text(node: Node, *, max_len: int = 40) -> str:
    """标题节点的可用文本：剥掉「更多>>」类噪声后收短。"""
    text = node.text_content()
    for noise in _MORE_NOISE:
        text = text.replace(noise, " ")
    return _short_label(text, max_len=max_len)


def _module_label(parent: Node) -> tuple[str, str]:
    """给列表容器找「栏目名」：向上最多三层，取**最近的前邻**标题型文本。

    政务门户的典型形状是 `<div class="hd"><h2>政策解读</h2></div>` 或标题文本节点
    紧跟在列表容器前面。找不到就如实返回空——区域无名时位置选择器仍在，但报告里
    要看得出来「这个名字是猜的还是页面上写的」(`label_source`)。
    """
    node = parent
    for _depth in range(3):
        if node.parent is None:
            break
        siblings = node.parent.children
        try:
            index = siblings.index(node)
        except ValueError:
            break
        for sib in reversed(siblings[:index]):
            if sib.tag == "#text":
                label = _heading_text(sib, max_len=20)
                if label:
                    return label, "preceding_text"
                continue
            if _looks_like_chrome(sib):
                continue  # 站头/导航/页脚：它的文本是导航串，不是栏目名
            if sib.tag in _HEADING_TAGS:
                if _is_nav_bar(sib):
                    continue  # h2 里塞着多个链接的是导航条，不是标题
                label = _heading_text(sib)
                if label:
                    return label, sib.tag
                continue
            classes = " ".join(sib.classes).lower()
            if any(hint in classes for hint in _HEADING_CLASS_HINTS):
                if _is_nav_bar(sib):
                    continue  # class 像标题但内容是导航条（多个栏目链接 + 一堆「更多>>」）
                label = _heading_text(sib)
                if label:
                    return label, "heading_div"
        node = node.parent
    return "", ""


def region_candidates(root: Node, *, base_url: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """命名内容区域（纯确定性）：一张页面上「有名字、可单独定位」的内容列表。

    解决「同页多个同签名列表」的归纳问题（典型：门户页的标签页「政府规章 / 市政府文件 /
    政策解读」并排）。两种形状：

    - **tab 标签页**：`ul[role=tablist]` 的第 N 个 `<li>` ↔ 文档序第 N 个
      `[role=tabpanel]`（数量必须相等，否则不配对——不猜）。区域名取 tab 的
      `wzades`/`aria-label`/链接文本；tab 里的 `<a href>` 同时作为「栏目页地址」证据
      （有独立栏目页时导入器优先把栏目页接成入口，比页内区域稳）。
    - **标题邻近模块**：列表容器向上三层内找得到标题型文本（h1-h4 / class 含
      tit/head/hd 的短文本节点）的同签名兄弟组。

    每个区域给出 `scope_selector` + `scope_index`（`EntrypointConfig` 的同一对字段）：
    scope 选择器在整页可能命中多个同签名模块，序号负责点名其中一个。无名模块不进
    区域清单——它们已由普通 `list_candidates` 覆盖，这里只归纳「能叫出名字」的。
    """
    regions: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    used_names: set[str] = set()  # 页内区域名唯一：筛选按名字匹配机构，克隆名（xx2/xx3）没法用

    def _scope_index_for(node: Node, selector: str) -> int | None:
        """节点在同选择器全部命中里的序号；只命中它一个时返回 None（配置更干净）。"""
        matches = select(root, selector)
        if len(matches) <= 1:
            return None
        try:
            return matches.index(node)
        except ValueError:
            return None

    # --- tab 标签页 ---
    for tablist in select(root, "ul[role=tablist]"):
        tabs = [child for child in tablist.children if child.tag == "li"]
        panels = [node for node in root.iter_elements() if (node.get("role") or "").strip() == "tabpanel"]
        if not tabs or not panels or len(panels) != len(tabs):
            continue  # 数量对不上就不配对（有些模板只给当前 tab 渲染面板）
        for tab, panel in zip(tabs, panels):
            name = _tab_label(tab)
            if not name or name in used_names:
                continue
            items = [child for child in panel.children if child.tag == "li"]
            links = [(_item, _link) for _item in items if (_link := _link_of(_item)) and _usable_href(_link)]
            if len(links) < 2:
                continue
            scope_selector = strip_nth_of_type(describe_selector(panel)).strip()
            if not scope_selector or len(scope_selector) > 120:
                continue
            key = (scope_selector, _scope_index_for(panel, scope_selector))
            if key in seen:
                continue
            seen.add(key)
            used_names.add(name)
            item, link = links[0]
            column_href = _usable_href(tab) if tab.tag == "a" else None
            if column_href is None:
                column_link = _link_of(tab)
                column_href = _usable_href(column_link) if column_link is not None else None
            regions.append(
                {
                    "kind": "tab",
                    "name": name,
                    "scope_selector": scope_selector,
                    "scope_index": key[1],
                    "list_selector": _child_signature(items[0]),
                    "item_count": len(items),
                    "sample_title": item.text_content().strip()[:60],
                    "sample_url": urljoin(base_url, _usable_href(link) or ""),
                    "column_url": urljoin(base_url, column_href) if column_href else None,
                    "label_source": "tab",
                }
            )

    # --- 标题邻近模块 ---
    for parent, signature, items, links, _avg in _content_groups(root):
        if (parent.get("role") or "").strip() == "tabpanel":
            continue  # 已是 tab 区域的面板，不重复归纳
        if any((item.get("role") or "").strip() == "tabpanel" for item in items):
            continue  # 面板集合（div.bd 下并排的几个 tabpanel）：各面板已由 tab 区域逐个
        # 归纳，这里再出一个「合并全部面板」的模块区域只会把拆分又搅回去
        name, label_source = _module_label(parent)
        if not name or name in used_names:
            continue  # 无名或名字已被同页其它区域占用：出克隆名不如不出（筛选按名字匹配）
        parent_selector = strip_nth_of_type(describe_selector(parent)).strip()
        if not parent_selector or len(parent_selector) > 120:
            continue
        # 只在「普通选择器会产生歧义」时才归纳模块区域：普通列表候选的选择器是
        # `parent > signature`，若它在整页命中的节点恰好就是这一组（数量相等），该选择器
        # 已经精确，区域不带增量；若命中更多（同签名兄弟组还有别的），scope+index 才是
        # 把这一组单独点名的机制。没有这条判据，普通栏目页会被归纳出与列表候选重复的
        # 区域（2026-10-16 回归用例：页脚块被命名并接成第二个入口，正是要避免的形状）。
        plain_hits = select(root, f"{parent_selector} > {signature}")
        if len(plain_hits) <= len(items):
            continue
        scope_index = _scope_index_for(parent, parent_selector)
        key = (parent_selector, scope_index)
        if key in seen:
            continue
        seen.add(key)
        used_names.add(name)
        item, link = links[0]
        regions.append(
            {
                "kind": "module",
                "name": name,
                "scope_selector": parent_selector,
                "scope_index": scope_index,
                "list_selector": signature,
                "item_count": len(items),
                "sample_title": item.text_content().strip()[:60],
                "sample_url": urljoin(base_url, _usable_href(link) or ""),
                "column_url": None,
                "label_source": label_source,
            }
        )

    return regions[:limit]


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
                node=node,
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


# ---------- 字段（标签 → 值）----------

# 一条候选的「值从哪来」：与 `config.FieldValueFrom` 同名，接入流程可以直接落成规则。
_FIELD_RELATIONS = ("label_child", "label_sibling", "inline")


def _next_element_sibling(node: Node) -> Node | None:
    parent = node.parent
    if parent is None:
        return None
    try:
        index = parent.children.index(node)
    except ValueError:
        return None
    for sibling in parent.children[index + 1 :]:
        if sibling.tag != "#text":
            return sibling
    return None


def _clean_value(text: str) -> str:
    return " ".join((text or "").split()).strip(" ：:")


def _infer_field(label: str, value: str) -> str:
    """标签优先；标签认不出时按值的形状兜底（日期 / 文号）。"""
    field = _FIELD_LABELS.get(label)
    if field:
        return field
    if _DOCNO_RE.search(value):
        return "doc_no"
    if _DATE_RE.search(value):
        return "publish_date"
    return "unknown"


def _field_record(
    *,
    field: str,
    label: str,
    selector: str,
    relation: str,
    value_from: str,
    value: str,
    score: float,
) -> dict[str, Any]:
    return {
        "kind": "field",
        "field": field,
        "label": label,
        "selector": selector,
        "relation": relation,
        "value_from": value_from,
        "sample_value": _clean_value(value)[:_FIELD_VALUE_MAX],
        "score": round(score, 1),
    }


def field_candidates(root: Node, *, limit: int = 40) -> list[dict[str, Any]]:
    """从页面上找「标签：值」关系，产出可直接转成 `FieldRule` 的候选。

    这是对旧 `meta_candidates` 的关键补充。旧实现只取节点**直接文本**去匹配日期/文号，
    于是下面三种最常见的政务模板全部漏掉：

    1. `<div><span class="fw-bold">文　　号：</span> 惠府〔2025〕13号</div>`
       —— 标签在子节点、值在父节点的直接文本里（`label_child`）；
    2. `<dt>发布机构</dt><dd>惠州市政府办公室</dd>`
       —— 标签与值分居兄弟节点（`label_sibling`）；
    3. `<div>发布日期：2025-03-10</div>`
       —— 标签与值同节点（`inline`）。

    每条候选给出 `selector`（定位**标签**节点）、`label`（归一后）、`value_from`
    （值的位置）、`sample_value`（实测值，供模型核对）。`field` 认得出就映射成
    标准字段名（`publish_date` / `doc_no` / `agency` / `index_no` / `theme`…），
    认不出为 `unknown`，标签原样保留。
    """
    found: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def add(record: dict[str, Any]) -> None:
        if not record.get("sample_value"):
            return
        key = (record["field"], record["label"], record["selector"], record["relation"])
        previous = found.get(key)
        if previous is None or record["score"] > previous["score"]:
            found[key] = record

    for node in root.iter_elements():
        if node.tag in SKIP_TAGS:
            continue
        own = _own_text(node)
        if not own or len(own) > _FIELD_VALUE_MAX:
            continue
        # 字段规则的执行引擎不支持伪类；同签名节点（多个 `dt`）由标签过滤区分，
        # 因此这里去掉 `:nth-of-type(n)`，而不是把引擎跑不了的选择器交出去。
        selector = strip_nth_of_type(describe_selector(node)).strip()
        if not selector or len(selector) > 120:
            continue

        # --- 形状 3：标签与值同节点（own text 以「标签：」开头）---
        inline = re.match(r"^\s*([^:：\n]{2,%d})\s*[:：]\s*(.+)$" % _FIELD_LABEL_MAX, own)
        if inline:
            label = normalize_label(inline.group(1))
            value = inline.group(2)
            cleaned = _clean_value(value)
            # 认得的标签 / 日期 / 文号一律收；认不得的标签只在「短标签 + 短值」时收——
            # 正文里的「工作要求：……」这类句子标签和值都很长，靠长度就能挡住，
            # 而「责任处室：市司法局」这种站点自定义字段不该因为是生面孔就被漏掉。
            unrecognized_but_field_like = (
                len(cleaned) <= _FIELD_UNKNOWN_VALUE_MAX
                and re.fullmatch(r"[一-龥A-Za-z0-9（）()·\-]{1,%d}" % _FIELD_LABEL_MAX, label)
                is not None
            )
            if (
                label
                and label not in _FIELD_LABEL_DENY
                and cleaned
                and (
                    label in _FIELD_LABELS
                    or _DATE_RE.search(value)
                    or _DOCNO_RE.search(value)
                    or unrecognized_but_field_like
                )
            ):
                add(
                    _field_record(
                        field=_infer_field(label, value),
                        label=label,
                        selector=selector,
                        relation="inline",
                        value_from="self_text",
                        value=value,
                        score=4.0 + (2.0 if label in _FIELD_LABELS else 0.0),
                    )
                )
            continue  # 同节点写法不再按「标签节点」处理

        # --- 形状 1/2：节点本身就是标签 ---
        # 冒号是「这里就是标签」的最强信号；但没有冒号的表格/定义列表同样常见
        # （`<dt>成文日期</dt><dd>…</dd>`、`<th>文号</th><td>…</td>`）。对无冒号
        # 的情形只在**结构明确**或标签能对上标准字段名时才继续——否则任何相邻节点
        # 都会被当成「标签 + 值」，并列的列表条目会互相污染。
        stripped = own.rstrip()
        has_colon = stripped.endswith((":", "："))
        label = normalize_label(stripped[:-1] if has_colon else stripped)
        if not label or len(label) > _FIELD_LABEL_MAX or label in _FIELD_LABEL_DENY:
            continue
        # 只看**标签侧**的标签名：`<dd>`/`<td>` 也可能是无冒号节点，把父容器算进来
        # 会让值节点自己当上标签（`dd` 的兄弟正好是下一个 `dt`）。
        structural = node.tag in ("dt", "th")
        if not has_colon and not (structural or label in _FIELD_LABELS):
            continue

        parent = node.parent
        if parent is not None:
            parent_own = _own_text(parent)
            value = strip_leading_label(parent_own, node)
            if _clean_value(value):
                add(
                    _field_record(
                        field=_infer_field(label, value),
                        label=label,
                        selector=selector,
                        relation="label_child",
                        value_from="parent_own_text",
                        value=value,
                        score=4.5 + (2.0 if label in _FIELD_LABELS else 0.0),
                    )
                )
                continue
            # 值可能是父节点里**另一个**子元素（`<div>发布日期：<span>2025-03-10</span></div>`）。
            # 只有当父节点没有别的元素子节点时才走这一步：`<dl><dt>…</dt><dd>…</dd>`
            # 的父节点会把整张表都拼进来，取 parent_text 必然污染其它字段。
            others = [
                child
                for child in parent.children
                if child.tag != "#text" and child is not node
            ]
            if not others:
                parent_text = strip_leading_label(parent.text_content(), node)
                value = _clean_value(parent_text)
                if value:
                    add(
                        _field_record(
                            field=_infer_field(label, value),
                            label=label,
                            selector=selector,
                            relation="label_child",
                            value_from="parent_text",
                            value=value,
                            score=4.0 + (2.0 if label in _FIELD_LABELS else 0.0),
                        )
                    )
                    continue

        sibling = _next_element_sibling(node)
        if sibling is not None:
            value = sibling.text_content()
            cleaned = _clean_value(value)
            # 下一个兄弟本身也像标签（以冒号结尾）时不当值：`<dt>索引号</dt><dt>…`
            # 这类残缺模板里，硬取只会把另一个标签写成值。
            looks_like_label = cleaned.endswith((":", "："))
            if cleaned and not looks_like_label and len(cleaned) <= _FIELD_VALUE_MAX:
                add(
                    _field_record(
                        field=_infer_field(label, cleaned),
                        label=label,
                        selector=selector,
                        relation="label_sibling",
                        value_from="next_sibling_text",
                        value=cleaned,
                        score=4.0 + (2.0 if label in _FIELD_LABELS else 0.0),
                    )
                )

    return sorted(found.values(), key=lambda r: -r["score"])[:limit]


# ---------- DOM 提纲（给模型看的分层结构）----------

# 提纲里跳过的页面骨架：这些子树的文字是导航/脚本，不是字段
_OUTLINE_SKIP_CLASSES = ("nav", "menu", "header", "footer", "crumb", "breadcrumb", "copyright")
_OUTLINE_MAX_TEXT = 100


def dom_outline(root: Node, *, max_nodes: int = 160, max_depth: int = 7) -> list[str]:
    """把 DOM 压成「缩进 + 标签/属性 + 直接文本」的提纲，供模型判断页面结构。

    只列**直接文本**（`own_text`）：这正是「标签在 `<span>`、值在父节点」这类形状
    能被看出来的地方——若只给 `text_content()`，标签和值会糊成一行，模型也看不出
    该用 `parent_own_text` 还是 `next_sibling_text`。节点数封顶，避免把整页塞进上下文。
    """
    lines: list[str] = []
    count = 0

    def walk(node: Node, depth: int) -> None:
        nonlocal count
        if count >= max_nodes or depth > max_depth:
            return
        for child in node.children:
            if child.tag == "#text" or child.tag in SKIP_TAGS:
                continue
            classes = " ".join(child.classes).lower()
            if any(token in classes for token in _OUTLINE_SKIP_CLASSES):
                continue
            desc = child.tag
            if child.get("id"):
                desc += f"#{child.get('id')}"
            if child.classes:
                desc += "".join(f".{c}" for c in child.classes[:3])
            own = _own_text(child)
            if own:
                own = own[:_OUTLINE_MAX_TEXT]
                lines.append(f"{'  ' * depth}{desc} | {own}")
            else:
                lines.append(f"{'  ' * depth}{desc}")
            count += 1
            walk(child, depth + 1)

    walk(root, 0)
    if count >= max_nodes:
        lines.append(f"…（提纲已截断到 {max_nodes} 个节点）")
    return lines


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


def search_forms(root: Node, base_url: str) -> list[dict[str, Any]]:
    """站内检索入口候选（有的话可以走检索而不是翻栏目，覆盖更全）。

    每项：`action`（提交地址；`None` = 页面里有检索输入框但没有 form，提交地址得另找
    ——比如看 JS 或浏览器的网络面板）、`field`（关键词参数名）、`method`（**post 当前
    引擎不支持**，只能如实报出来）、`params`（表单里的隐藏参数与下拉框首选项，TRS 系的
    `channelid` 就在这里；不填 = 页面没给可复用参数）。

    这些拼起来就是一个可试的检索地址——`SearchConfig` 的字段正是照它填的，所以它必须
    **报实测值**，不能替站点猜参数名。`params` 里的下拉首选项是启发式：填错了会在
    「按这个地址真取一页」那一步暴露出来（取不到条目就不采纳），不会静默进配置。
    """
    out: list[dict[str, Any]] = []
    for form in select(root, "form"):
        action = (form.get("action") or "").strip()
        method = (form.get("method") or "get").strip().lower() or "get"
        params: dict[str, str] = {}
        for hidden in select(form, "input"):
            input_type = (hidden.get("type") or "text").lower()
            name = (hidden.get("name") or "").strip()
            if input_type == "hidden" and name:
                params[name] = (hidden.get("value") or "").strip()
        for select_node in select(form, "select"):
            name = (select_node.get("name") or "").strip()
            first = select_one(select_node, "option")
            if name and first is not None:
                value = (first.get("value") or first.text_content() or "").strip()
                if value:
                    params[name] = value
        for field_node in select(form, "input"):
            name = (field_node.get("name") or "").strip()
            input_type = (field_node.get("type") or "text").lower()
            if input_type in ("text", "search") and name:
                row = {
                    "action": urljoin(base_url, action) if action else base_url,
                    "field": name,
                    "method": method,
                    "params": params,
                }
                if row not in out:
                    out.append(row)
    if not out:
        # 现代站点常见「输入框不在 <form> 里、提交由 JS 拼地址」：这里如实报出输入框存在，
        # 但**不猜**提交地址（猜一个 /search 只会制造一条看着能用、实际取不到数的入口）。
        for field_node in select(root, 'input[type="search"], input[type="text"]'):
            name = (field_node.get("name") or "").strip()
            if name and name.lower() in _SEARCH_FIELD_HINTS:
                out.append({"action": None, "field": name, "method": None, "params": {}})
    return out[:5]


# ---------- 汇总 ----------


def looks_like_js_shell(html: str) -> bool:
    """静态取回的 HTML 是否疑似「JS 渲染空壳」。

    空壳特征：HTML 里躺着大量 `<script>`，但解析出的**可见文本**极少——正文和列表都
    要等前端 JS 异步填充，httpx 拿到的这份里没有任何可解析的内容。这类站点静态引擎
    （`engine=html_list`）永远抓不到东西，应走无头浏览器来源（`engine=browser_driven`）。

    这是**信号不是判决**：故意极简的页面（如整页一个 PDF 阅读器）也会命中，报告措辞
    用「疑似」，由试运行做最终裁判。

    门槛说明：script 只要求 ≥1 即可——现代前端常把 JS 打包成**单个 bundle**（如
    Vue 的 `index-xxxx.js`），数标签 ≥3 对这种站天然失效；真正的兜底证据是
    `<noscript>` 里那句「请启用 JavaScript」。
    """
    root = parse_html(html)
    text_len = len(root.text_content().strip())
    if text_len >= 300:
        return False
    if select(root, "script"):
        return True
    # <noscript> 兜底：站点自己声明"没有 JS 就不能正常工作"——比数 script 标签可靠
    noscript_text = " ".join(
        node.text_content().strip().lower() for node in select(root, "noscript")
    )
    return "javascript" in noscript_text


def analyze_page(
    html: str, url: str, *, listed_as: str = "page", include_outline: bool = False
) -> dict[str, Any]:
    """一页 HTML → 结构分析报告（`listed_as` 只影响输出里的自我标注）。

    返回的每一条都是**候选**：`evidence` 是关键指标，采纳与否由试运行验证。

    `field_candidates` / `dom_outline` 是给模型补配置用的：前者把「标签 → 值」关系
    实测出来，后者是带直接文本的压缩 DOM——模型据此能判断该用哪种 `value_from`，
    而不是只看整段 `text_content()` 猜。`include_outline=False` 可省掉提纲（列表页
    分析不需要它，省上下文）。
    """
    root = parse_html(html)
    meta = meta_candidates(root)
    page_title = select_one(root, "title")
    report: dict[str, Any] = {
        "listed_as": listed_as,
        "page_title": (page_title.text_content().strip() if page_title else "")[:120],
        "text_len": len(root.text_content()),
        # 空壳信号：见 looks_like_js_shell——「疑似需要无头浏览器取数」，接入时据此走
        # engine=browser_driven 路径；只是信号，最终以试运行为准
        "js_render_suspected": looks_like_js_shell(html),
        "list_candidates": [c.as_dict() for c in list_candidates(root, base_url=url)],
        "regions": region_candidates(root, base_url=url),
        "detail_candidates": [c.as_dict() for c in detail_candidates(root)],
        "meta_candidates": {name: [c.as_dict() for c in items] for name, items in meta.items()},
        "field_candidates": field_candidates(root),
        "pagination": pagination_candidates(root, url),
        "columns": column_links(root, url),
        "feeds": feeds_and_sitemaps(root, url),
        "search_forms": search_forms(root, url),
        "attachments": sorted(
            {urljoin(url, _usable_href(a) or "") for a in select(root, "a[href]") if _is_attachment(a)}
        )[:20],
    }
    if include_outline:
        report["dom_outline"] = dom_outline(root)
    return report


_ATTACHMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".ofd", ".wps")


def _is_attachment(anchor: Node) -> bool:
    href = (_usable_href(anchor) or "").lower()
    return href.endswith(_ATTACHMENT_SUFFIXES)
