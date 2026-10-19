"""配置驱动的通用抓取源（`HtmlListSource`）：一份 `SourceConfig` 顶一个站点。

这是「新增网站 ≠ 新增代码」的落点——它把 `PolicySource` 的五件事全部按配置解释：

- `search_plan`  ：关键词 × 入口（栏目）扇出；**不套用 gov.cn 的文种白名单**
  （文种是国家级检索接口的参数，栏目列表页给不了），传入 `doc_types` 会告警并忽略；
- `search`       ：按每个入口自己的翻页规则取列表页 → 解析出条目（URL/标题/日期）；
- `validate_item`：关键词复核 + 时间窗口复核（口径由 `keyword_match` /
  `drop_undated_when_windowed` 决定，见 `sources/config.py`）；
- `fetch_fulltext`：取详情页 → 按候选容器链抽正文 → 抓元信息与附件清单；
- `normalize_item`：原始 dict → `PolicyItem`（含零宽字符清理，与 gov.cn 同一套）。

**列表页缓存**：同一次检索里 `search_plan` 会为每个关键词各发一轮，若每轮都重新取
同一张列表页，既慢又对站点不礼貌。这里用 TTL 缓存吸收重复取数（条目上限见 `_CACHE_MAX`）。

解析本身是纯函数（`parse_list_page` / `parse_detail_page`），可以直接喂 HTML 字符串测试，
不依赖网络与数据库。
"""

from __future__ import annotations

import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from app.config import get_settings
from app.sources import net
from app.sources.base import FulltextResult, PolicyItem, PolicySource
from app.sources.config import DetailConfig, EntrypointConfig, PaginationConfig, SourceConfig
from app.sources.field_rules import extract_fields
from app.sources.htmlparse import Node, parse_html, select, select_one
from app.sources.util import parse_date

logger = logging.getLogger(__name__)

# 附件按扩展名启发式识别（未配 attachment_selector 时用）
_ATTACHMENT_EXTS = ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "ofd", "wps")
_ATTACHMENT_HEURISTIC = ", ".join(f'a[href$=".{ext}"]' for ext in _ATTACHMENT_EXTS)

# 元信息常写成「发布日期：2026-10-13」「来源：浙江省政府办公厅」——去掉短标签前缀再判
_META_LABEL_RE = re.compile(r"^\s*[一-龥A-Za-z]{2,8}\s*[:：]\s*")

_CACHE_MAX = 64  # 列表页缓存条目上限（简单的容量+TTL 双限，规避无界增长）

# 字段规则里的**保留字段名**：有列可用，其余进 metadata。
# `date` 与 `publish_date` 视为同一个东西（列表/详情两处习惯叫法不同）。
_RESERVED_FIELD_NAMES = {"title", "agency", "doc_no", "date", "publish_date"}


def _strip_label(text: str) -> str:
    """去掉元信息里的中文标签前缀（「发布日期：2026-10-13」→「2026-10-13」）。"""
    return _META_LABEL_RE.sub("", (text or "").strip()).strip()


def _usable_href(node: Node) -> str | None:
    """节点上可用于跳转的 href；占位符 / 非 http 的一律不算。"""
    href = (node.get("href") or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#", "tel:")):
        return None
    return href


def _pick_item_link(container: Node, link_selector: str, title: str) -> Node | None:
    """在条目容器里挑出「那一个」条目链接。

    一个 `<li>` 里常常不止一个 `<a>`（标题 + 缩略图 +「更多」）。取第一个命中的是碰
    运气：缩略图或「更多」排在前面，条目就指到错的地方去了。这里按可靠性排序——

    1. 文本与已解析出的标题一致的链接（最可靠；配了 `title_selector` 时几乎必中）；
    2. 否则取文本最长的那个：标题链接几乎总是条目里最长的一段文字，而缩略图链接
       没有文本、「更多」只有两个字。

    同一口径下按文档序取第一个（`max` 返回首个最大值），结果稳定可复现。
    """
    candidates = select(container, link_selector) if link_selector else []
    if container.tag == "a":
        # 条目容器本身就是一个 <a>：`select` 只遍历子孙、不含自己，得手动补进来
        candidates.insert(0, container)
    usable = [(node, _usable_href(node)) for node in candidates]
    usable = [(node, href) for node, href in usable if href]
    if not usable:
        return None
    if title:
        want = " ".join(title.split())
        for node, _ in usable:
            if " ".join(node.text_content().split()) == want:
                return node
    return max(usable, key=lambda pair: len(" ".join(pair[0].text_content().split())))[0]


# ---------- 纯函数：列表页 URL 构造 ----------


def _select_entry_containers(root: Node, entry: EntrypointConfig) -> list[Node]:
    """按入口配置取出条目容器列表。

    配了 `scope_selector` 时先定位作用域容器（可命中多个），再在各容器的**子树内**跑
    `list_selector`；`scope_index` 非空时只取命中的第几个（0 起）——这是「同页多个同签名
    列表只取其中一个」的机制（标签页面板 / 并列栏目模块），不依赖 CSS 位置伪类
    （引擎的选择器子集本就不支持 `:nth-of-type`，见 `htmlparse.selector_supported`）。
    """
    if not entry.scope_selector:
        return select(root, entry.list_selector)
    scopes = select(root, entry.scope_selector)
    if entry.scope_index is not None:
        if entry.scope_index >= len(scopes):
            return []
        scopes = [scopes[entry.scope_index]]
    return [container for scope in scopes for container in select(scope, entry.list_selector)]


def _merge_query(url: str, params: list[tuple[str, str]]) -> str:
    """把 `params` 并进 `url` 的查询串：同名参数**替换**，其余保留。"""
    parts = urlsplit(url)
    pairs = [p for p in parts.query.split("&") if p] if parts.query else []
    names = {name for name, _ in params}
    kept = [p for p in pairs if p.split("=", 1)[0] not in names]
    added = [f"{quote(str(name), safe='')}={quote(str(value), safe='')}" for name, value in params]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join([*kept, *added]), parts.fragment))


def _paginate(url: str, pagination: PaginationConfig, page: int) -> str:
    """在**已经带上检索参数**的地址上套翻页规则（第 2 页起）。"""
    value = pagination.start + (page - 1) * pagination.step
    if pagination.kind == "path_index":
        template = pagination.path_template or "{page}"
        return urljoin(url, template.format(page=value))
    # query_param：保留原查询串（含关键词与日期参数），替换/追加分页参数
    parts = urlsplit(url)
    new_query = f"{pagination.param}={value}"
    if parts.query:
        kept = [pair for pair in parts.query.split("&") if not pair.startswith(f"{pagination.param}=")]
        new_query = "&".join([*kept, new_query]) if kept else new_query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def build_list_url(entry: EntrypointConfig, page: int) -> str | None:
    """按入口的翻页规则算出第 `page` 页的地址；该页不存在时返回 None。

    `page` 从 1 计。`none` 规则只有第 1 页（返回 None = 该入口翻到底，
    与 gov.cn 越界页回空列表等价，流水线会据此把该组合标记为耗尽）。

    **第 1 页就是入口地址本身**，不由翻页规则反推：入口 URL 是勘察时**实际取过**的那个
    地址（配置里写的、验证过能出条目的那一个），而翻页规则只是从第 2 页起的链接反推出来的。
    真实站点的第 1 页往往没有页码（`index.html` 而不是 `index_1.html`），拿模板去套第 1 页
    只会得到 404——所以这里以入口地址为准，规则只作用于第 2 页起。
    `pagination.start` 记录的仍是**站点对第 1 页的取值口径**（0 / 1 起），第 2 页取
    `start + step`（见 `analyze.pagination_candidates` 里 `start_reason` 的推断）。
    """
    pagination = entry.pagination
    if page < 1 or page > pagination.max_pages:
        return None
    if page == 1:
        return entry.url
    if pagination.kind == "none":
        return None
    return _paginate(entry.url, pagination, page)


def build_search_url(
    entry: EntrypointConfig,
    *,
    keyword: str | None,
    date_from: date | None,
    date_to: date | None,
    page: int,
) -> str | None:
    """检索型入口的第 `page` 页地址：把关键词/日期按 `entry.search` 的约定拼进查询串。

    返回值 None 有三种含义，调用方一视同仁（这一轮该入口不取数）：

    - **没给关键词**：检索页在没有关键词参数时返回的通常是空结果页或首页，不是可浏览的
      路径，所以无关键词的那一轮直接跳过检索入口（栏目入口照旧翻）；
    - 页码越界（该入口翻到底）；
    - **单页检索**（`pagination.kind == "none"`）的第 2 页起：与 `build_list_url` 同一口径。
      不加这条守卫的话，第 2 页会拼出一个站点根本不认的 `page=2`——站点忽略它、把第 1 页
      原样回一遍，取回来的全是重复 URL（被 `seen` 滤掉），组合永远不进「翻到底」，
      白白把检索预算耗在同一张页面上。

    日期只在站点检索页**确实接受**该参数时才拼（未配 `date_from_param` / `date_to_param`
    = 该站检索页不支持按日期收窄，日期仍由 `validate_item` 取回后复核）。
    """
    search = entry.search
    if search is None or page < 1 or page > entry.pagination.max_pages:
        return None
    if page > 1 and entry.pagination.kind == "none":
        return None
    if not (keyword or "").strip():
        return None
    params: list[tuple[str, str]] = [(str(k), str(v)) for k, v in search.fixed_params.items()]
    params.append((search.keyword_param, keyword.strip()))
    if date_from and search.date_from_param:
        params.append((search.date_from_param, date_from.strftime(search.date_format)))
    if date_to and search.date_to_param:
        params.append((search.date_to_param, date_to.strftime(search.date_format)))
    base = _merge_query(entry.url, params)
    if page == 1:
        return base
    return _paginate(base, entry.pagination, page)


def build_entry_url(
    entry: EntrypointConfig,
    *,
    keyword: str | None,
    date_from: date | None,
    date_to: date | None,
    page: int,
) -> str | None:
    """入口第 `page` 页的地址，**唯一的取数入口**（检索型 / 栏目型都在这里分流）。

    试运行和正式抓取都走它，两边口径才一致：`services/onboarding.py` 的入口探测
    也必须用同一个函数，否则「试运行过了、正式抓取取不到」这种分歧迟早出现。
    """
    if entry.is_search:
        return build_search_url(
            entry, keyword=keyword, date_from=date_from, date_to=date_to, page=page
        )
    return build_list_url(entry, page)


def parse_list_page(
    html: str, entry: EntrypointConfig, base_url: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """列表页 HTML → 条目 dict 列表（`title` / `url` / `date` / `entrypoint`）。

    只做结构解析，不做关键词与日期过滤（那是 `validate_item` 的事）；
    页内按绝对 URL 去重（同一个链接在「标题 + 更多」里出现两次很常见）。
    """
    root = parse_html(html)
    containers = _select_entry_containers(root, entry)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        # 标题先定：配了 `title_selector` 就用它，取不到留给链接文本兜底。先定标题也是
        # 为了让 `_pick_item_link` 能用「文本 == 标题」把真正的条目链接挑出来。
        title = ""
        if entry.title_selector:
            title_node = select_one(container, entry.title_selector)
            if title_node is not None:
                title = title_node.text_content()

        link = _pick_item_link(container, entry.link_selector, title)
        if link is None:
            continue
        url = urljoin(base_url, _usable_href(link) or "")
        if not urlsplit(url).scheme.startswith("http") or url in seen:
            continue

        if not title:
            title = link.text_content()
        if not title:
            title = link.get("title") or ""
        title = " ".join(title.split())
        if not title:
            continue

        item_date = None
        if entry.date_selector:
            date_node = select_one(container, entry.date_selector)
            if date_node is not None:
                item_date = _strip_label(date_node.text_content()) or None
        elif link.get("title") and parse_date(link.get("title")):
            item_date = link.get("title")  # 有些站把日期放进 title 属性

        # 条目级字段规则（新口径）：在**条目容器**内执行，取到的标准字段覆盖上面的
        # 旧选择器结果，其余字段进 metadata。规则链内部按顺序 fallback。
        agency = doc_no = None
        metadata: dict[str, str] = {}
        if entry.item_fields:
            extracted = extract_fields(container, entry.item_fields)
            title = extracted.get("title") or title
            agency = extracted.get("agency")
            doc_no = extracted.get("doc_no")
            item_date = (
                extracted.get("date") or extracted.get("publish_date") or item_date
            )
            metadata = {
                key: value
                for key, value in extracted.items()
                if key not in _RESERVED_FIELD_NAMES
            }

        if not title:
            continue

        seen.add(url)
        item: dict[str, Any] = {
            "title": title,
            "url": url,
            "date": item_date,
            "entrypoint": entry.name,
            "pagination_url": base_url,
        }
        if agency:
            item["agency"] = agency
        if doc_no:
            item["doc_no"] = doc_no
        if metadata:
            item["metadata"] = metadata
        items.append(item)
        if limit is not None and len(items) >= limit:
            break
    return items


# ---------- 纯函数：详情页解析 ----------


@dataclass
class Attachment:
    """详情页里发现的附件（正文之外的原始文件）。"""

    url: str
    name: str
    ext: str
    size_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "name": self.name, "ext": self.ext, "size_text": self.size_text}


@dataclass
class DetailResult:
    """详情页解析结果；`body` 为空表示正文容器全部落空（解析失败，交由调用方记失败）。"""

    url: str
    body: str = ""
    title: str | None = None
    agency: str | None = None
    doc_no: str | None = None
    date_text: str | None = None
    container_selector: str | None = None
    image_count: int = 0
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 字段规则取到的非保留字段（索引号 / 成文日期 / 主题分类 / 发文机关…），
    # 原样透传到 `FulltextResult.metadata` → `policy_document.metadata`。
    extra: dict[str, str] = field(default_factory=dict)


def _pick_container(root: Node, selectors: list[str]) -> tuple[Node | None, str | None]:
    """按候选链取正文容器：**第一个命中的选择器**里取文本最长的一个节点。

    先按选择器优先级、再按文本长度，是为了对付「正文 + 打印版副本」这类同选择器
    命中多个的情况；选择器之间不比较长度，否则一个泛化的兜底选择器会把正文挤掉。
    """
    for selector in selectors:
        matches = select(root, selector)
        if not matches:
            continue
        best = max(matches, key=lambda n: len(n.text_content()))
        return best, selector
    return None, None


# 启发式兜底的最低正文长度。低于它更可能是侧栏 / 相关阅读块而不是政策正文；
# 200 与 `DetailConfig.min_body_chars` 的默认值同口径（试运行判定「正文过短」的那条线）。
_FALLBACK_MIN_CHARS = 200


def _fallback_container(root: Node) -> tuple[Node | None, str | None]:
    """配置的选择器全落空时的兜底：用结构打分挑一块最像正文的节点。

    复用 `analyze.detail_candidates`（文本量为主、常见正文 id/class 加权、链接密度过高
    降权）。它**在候选阶段就排除了 `<html>` / `<body>`**，所以这仍然不是「退化成整页
    文本」——是打分选出的具体一块，且必须在 `warnings` 里标明未经确认。

    宁可失败也不给错内容：不达 `_FALLBACK_MIN_CHARS` 一律当兜底失败。
    """
    # 局部导入：解析热路径只在「配置的选择器全落空」时才付出这份开销
    from app.sources.analyze import detail_candidates

    for candidate in detail_candidates(root, limit=5):
        if candidate.node is None:
            continue
        if int(candidate.evidence.get("text_len") or 0) < _FALLBACK_MIN_CHARS:
            continue
        return candidate.node, candidate.selector
    return None, None


def _text_of(root: Node, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = select_one(root, selector)
        if node is not None:
            text = " ".join(node.text_content().split())
            if text:
                return text
    return None


def date_texts_by_selector(html: str, selectors: list[str]) -> dict[str, str | None]:
    """一页 HTML × 一串日期候选 → **每个候选各自**的取值（与运行时同一口径）。

    `parse_detail_page` 只留下「第一个取到非空文本的候选」的结果，取不到的候选是看不见的；
    接入流程要用**跨页比对**认出「在每一篇样本上都取到同一个日期」的候选（那是页脚/模板
    元素，不是文章自己的日期），就必须把每个候选分别量一遍。所以这里逐个跑
    `select_one` → `_strip_label`，与 `result.date_text = _strip_label(_text_of(...))`
    完全相同的一条链——比对口径与线上取值口径一旦不同，这个核对就没有意义。
    """
    root = parse_html(html)
    out: dict[str, str | None] = {}
    for selector in selectors:
        out[selector] = _strip_label(_text_of(root, [selector]) or "") or None
    return out


def parse_detail_page(html: str, detail: DetailConfig, url: str) -> DetailResult:
    """详情页 HTML → `DetailResult`（正文 + 元信息 + 附件）。

    元信息**取不到就是 None**，绝不猜：三阶段验收里「元数据命中率」必须反映真实情况
    （地方站的字段名千差万别，猜错比缺失更糟）。
    """
    result = DetailResult(url=url)
    root = parse_html(html)

    container, matched_selector = _pick_container(root, detail.container_selectors)
    if container is None:
        # 兜底：配置的选择器一个都没命中时，用结构打分挑一块，而不是直接判失败。
        # 但必须留痕——这段正文是**未经确认**的，验收报告里要看得见。
        container, matched_selector = _fallback_container(root)
        if container is not None:
            result.warnings.append(
                "配置的正文容器候选全部落空（选择器：%s），已用结构启发式兜底选中「%s」；"
                "该段正文未经确认，建议人工核对后写进 container_selectors"
                % ("、".join(detail.container_selectors) or "（未配置）", matched_selector)
            )
        else:
            result.warnings.append(
                "正文容器候选全部落空（选择器：%s），启发式兜底也没找到达标正文——该页正文解析失败"
                % ("、".join(detail.container_selectors) or "（未配置）")
            )
            return result
    result.container_selector = matched_selector

    # 语义化字段规则优先（新口径）。在**整页**上执行而不是正文容器内：政务站的
    # 元信息块（索引号 / 发文字号 / 成文日期）常常在正文容器之外。
    # 取不到的保留字段再回落到旧选择器链，保证老配置行为不变。
    field_values = extract_fields(root, detail.fields) if detail.fields else {}
    result.extra = {
        key: value
        for key, value in field_values.items()
        if key not in _RESERVED_FIELD_NAMES
    }

    # 附件清单**先扫**，再摘掉 drop_selectors：附件区（`div.attach` 之类）通常要
    # 从正文里剔除，但「有哪些附件」正是接入要拿到的信息——先摘后扫会把附件一起抹掉。
    selector = detail.attachment_selector or _ATTACHMENT_HEURISTIC
    seen: set[str] = set()
    for node in select(root, selector):
        href = (node.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        name = " ".join(node.text_content().split()) or absolute.rsplit("/", 1)[-1]
        ext = absolute.rsplit(".", 1)[-1].lower() if "." in absolute else ""
        result.attachments.append(Attachment(url=absolute, name=name[:200], ext=ext))

    for selector in detail.drop_selectors:
        for node in select(container, selector):
            node.detach()

    result.image_count = sum(1 for node in container.iter_elements() if node.tag == "img")
    result.body = container.text_content()

    result.title = field_values.get("title") or _text_of(root, detail.title_selectors) or _text_of(
        container, ["h1", "h2"]
    )
    if not result.title:
        head_title = select_one(root, "title")
        result.title = " ".join(head_title.text_content().split()) if head_title else None
    result.agency = (
        field_values.get("agency")
        or _strip_label(_text_of(root, detail.agency_selectors) or "")
        or None
    )
    result.doc_no = (
        field_values.get("doc_no")
        or _strip_label(_text_of(root, detail.doc_no_selectors) or "")
        or None
    )
    # `normalize=date` 的规则已产出 ISO；旧选择器的文本还可能带标签，保持原口径
    result.date_text = (
        field_values.get("date")
        or field_values.get("publish_date")
        or _strip_label(_text_of(root, detail.date_selectors) or "")
        or None
    )
    return result


# ---------- 抓取源适配器 ----------


class HtmlListSource(PolicySource):
    """由 `SourceConfig` 驱动的抓取源（`engine = html_list`）。"""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.key = config.key
        self.label = config.label
        self.fetched_by = f"html_list:{config.key}"
        # 配置化来源不套用 gov.cn 的文种白名单（见模块 docstring）
        self.doc_types: tuple[str, ...] = ()
        self._cache: dict[str, tuple[float, str]] = {}
        self._cache_ttl = max(0, get_settings().source_list_cache_ttl)

    # ---------- PolicySource 契约 ----------

    def search_plan(
        self,
        keywords: list[str],
        agencies: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[tuple[str | None, str | None, str | None]]:
        if doc_types:
            logger.warning(
                "配置化来源「%s」不支持文种筛选（%s），本轮已忽略——文种靠文号在详情页解析",
                self.label,
                "、".join(doc_types),
            )
        return list(itertools.product(keywords or [None], agencies or [None], [None]))

    async def search(
        self,
        *,
        keyword: str | None,
        agency: str | None,
        doc_type: str | None,
        page: int,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        timeout: int,
    ) -> list[dict[str, Any]]:
        entries = [ep for ep in self.config.entrypoints if agency is None or ep.name == agency]
        if not entries:
            return []

        items: list[dict[str, Any]] = []
        for entry in entries:
            # 检索型入口在这里把关键词/日期拼进站点检索页的查询串；栏目型入口照旧翻页。
            # 检索型入口在**没给关键词**时返回 None → 跳过（见 build_search_url）。
            url = build_entry_url(
                entry, keyword=keyword, date_from=date_from, date_to=date_to, page=page
            )
            if url is None:
                continue  # 该入口翻到底 / 检索入口本轮无关键词（等价于越界页回空列表）
            try:
                html = await self._get_text(url, timeout)
            except net.UnsafeURLError:
                raise
            except net.FetchError as exc:
                # 单个入口取不到不该让整个任务失败：记为翻空并在日志里留痕。
                # 全部入口都取不到时外层会因 0 结果走 fail，用户看到的仍是可读文案。
                logger.warning("来源「%s」入口「%s」第 %s 页取数失败：%s", self.label, entry.name, page, exc)
                continue
            items.extend(parse_list_page(html, entry, url, limit=limit))
        return items

    def validate_item(
        self,
        item: dict[str, Any],
        *,
        keyword: str | None,
        date_from: date | None,
        date_to: date | None,
        facet_active: bool,
    ) -> bool:
        title = str(item.get("title") or "").strip()
        if keyword:
            if self.config.keyword_match == "any_char":
                if keyword not in title and not any(ch in title for ch in keyword):
                    return False
            elif keyword not in title:
                return False
        item_date = parse_date(item.get("date"))
        if item_date is None:
            # 列表页没有日期很常见（日期在详情页）；窗口生效时是否丢弃由配置决定
            return not (self.config.drop_undated_when_windowed and (date_from or date_to))
        if date_from and item_date < date_from:
            return False
        if date_to and item_date > date_to:
            return False
        return True

    async def fetch_fulltext(
        self, url: str, policy_id: int | None = None, *, timeout: int | None = None
    ) -> FulltextResult:
        text = await self._get_text(
            url, timeout if timeout else get_settings().pipeline_mcp_timeout
        )
        detail = parse_detail_page(text, self.config.detail, url)
        if not detail.body:
            # 容器全落空且兜底也不达标 = 解析失败。**不退化成整页文本**（那会把导航/脚本当正文入库）
            raise ValueError("；".join(detail.warnings) or "正文解析失败（容器选择器未命中）")
        if detail.warnings:
            # 走启发式兜底（选择器全落空但挑出了正文）才会到这里：不阻断链路，但必须留痕，
            # 否则「已降级」这件事在真正的抓取链路上完全不可见（接入报告那条路才带 warnings）
            logger.warning(
                "来源「%s」详情页解析告警（%s）：%s", self.label, url, "；".join(detail.warnings)
            )
        publish_date = parse_date(detail.date_text)
        # 结构解析没拿到日期时，把整页文本留给提取阶段的模型兜底（日期常在正文容器外）。
        # 只在缺日期时计算：有日期就不为这份可能很大的文本白付一次解析与内存。
        page_text = None
        if publish_date is None:
            page_text = " ".join(parse_html(text).text_content().split())
        return FulltextResult(
            body=detail.body,
            title=detail.title,
            issuer=detail.agency,
            doc_no=detail.doc_no,
            date=publish_date,
            image_count=detail.image_count,
            attachments=[a.as_dict() for a in detail.attachments],
            metadata=detail.extra,
            page_text=page_text,
        )

    def fulltext_available(self) -> bool:
        # 配置化来源只需要 HTTP，不依赖 MCP 是否加载
        return True

    def filter_capabilities(self) -> dict[str, Any]:
        """筛选能力：取决于入口里有没有**站内检索**入口（见 `config.capability_summary`）。"""
        return self.config.capabilities()

    def normalize_item(self, raw: dict[str, Any]) -> PolicyItem | None:
        # 局部导入：fulltext_clean 经 schemas 反向依赖 sources，顶层导入会成环
        from app.pipeline.fulltext_clean import strip_zero_width

        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not title or not url:
            return None
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        extra = {
            str(key): strip_zero_width(str(value)).strip()
            for key, value in metadata.items()
            if str(value or "").strip()
        }
        return PolicyItem(
            title=title,
            url=url,
            date=parse_date(raw.get("date")),
            agency=strip_zero_width(str(raw.get("agency") or "")).strip() or None,
            doc_no=strip_zero_width(str(raw.get("doc_no") or "")).strip() or None,
            extra=extra,
            # 来源目录 = 命中的栏目名（成果库按此展示「来源栏目」）
            catalog_key=str(raw.get("entrypoint") or "").strip() or None,
            catalog=str(raw.get("entrypoint") or "").strip() or None,
            judge_tagged=raw.get("judge_tagged"),
            judge_prob=raw.get("judge_prob"),
        )

    # ---------- 取数与缓存 ----------

    async def _get_text(self, url: str, timeout: float | int) -> str:
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]
        result = await net.fetch_text(
            url,
            allowed_domains=self.config.scope.allowed_domains,
            timeout=float(timeout),
        )
        if len(self._cache) >= _CACHE_MAX:
            oldest = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest, None)
        self._cache[url] = (now, result.text)
        return result.text

    def clear_cache(self) -> None:
        """清掉列表页缓存（重新勘察 / 配置变更后调用，避免拿到改版前的旧页面）。"""
        self._cache.clear()
