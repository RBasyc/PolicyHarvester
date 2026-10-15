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
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.config import get_settings
from app.sources import net
from app.sources.base import FulltextResult, PolicyItem, PolicySource
from app.sources.config import DetailConfig, EntrypointConfig, SourceConfig
from app.sources.htmlparse import Node, parse_html, select, select_one
from app.sources.util import parse_date

logger = logging.getLogger(__name__)

# 附件按扩展名启发式识别（未配 attachment_selector 时用）
_ATTACHMENT_EXTS = ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "ofd", "wps")
_ATTACHMENT_HEURISTIC = ", ".join(f'a[href$=".{ext}"]' for ext in _ATTACHMENT_EXTS)

# 元信息常写成「发布日期：2026-10-13」「来源：浙江省政府办公厅」——去掉短标签前缀再判
_META_LABEL_RE = re.compile(r"^\s*[一-龥A-Za-z]{2,8}\s*[:：]\s*")

_CACHE_MAX = 64  # 列表页缓存条目上限（简单的容量+TTL 双限，规避无界增长）


def _strip_label(text: str) -> str:
    """去掉元信息里的中文标签前缀（「发布日期：2026-10-13」→「2026-10-13」）。"""
    return _META_LABEL_RE.sub("", (text or "").strip()).strip()


# ---------- 纯函数：列表页解析 ----------


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
    value = pagination.start + (page - 1) * pagination.step
    if pagination.kind == "path_index":
        template = pagination.path_template or "{page}"
        return urljoin(entry.url, template.format(page=value))
    # query_param：保留原查询串，替换/追加分页参数
    parts = urlsplit(entry.url)
    query = parts.query
    new_query = f"{pagination.param}={value}"
    if query:
        kept = [pair for pair in query.split("&") if not pair.startswith(f"{pagination.param}=")]
        new_query = "&".join([*kept, new_query]) if kept else new_query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def parse_list_page(
    html: str, entry: EntrypointConfig, base_url: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """列表页 HTML → 条目 dict 列表（`title` / `url` / `date` / `entrypoint`）。

    只做结构解析，不做关键词与日期过滤（那是 `validate_item` 的事）；
    页内按绝对 URL 去重（同一个链接在「标题 + 更多」里出现两次很常见）。
    """
    root = parse_html(html)
    containers = select(root, entry.list_selector)
    if not containers:
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        link = select_one(container, entry.link_selector) if entry.link_selector else None
        if link is None and container.tag == "a":
            link = container  # 条目容器本身就是一个 <a>
        if link is None:
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        url = urljoin(base_url, href)
        if not urlsplit(url).scheme.startswith("http") or url in seen:
            continue

        title = ""
        if entry.title_selector:
            title_node = select_one(container, entry.title_selector)
            if title_node is not None:
                title = title_node.text_content()
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

        seen.add(url)
        items.append(
            {
                "title": title,
                "url": url,
                "date": item_date,
                "entrypoint": entry.name,
                "pagination_url": base_url,
            }
        )
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


def _text_of(root: Node, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = select_one(root, selector)
        if node is not None:
            text = " ".join(node.text_content().split())
            if text:
                return text
    return None


def parse_detail_page(html: str, detail: DetailConfig, url: str) -> DetailResult:
    """详情页 HTML → `DetailResult`（正文 + 元信息 + 附件）。

    元信息**取不到就是 None**，绝不猜：三阶段验收里「元数据命中率」必须反映真实情况
    （地方站的字段名千差万别，猜错比缺失更糟）。
    """
    result = DetailResult(url=url)
    root = parse_html(html)

    container, matched_selector = _pick_container(root, detail.container_selectors)
    if container is None:
        result.warnings.append(
            "正文容器候选全部落空（选择器：%s）——该页正文解析失败"
            % ("、".join(detail.container_selectors) or "（未配置）")
        )
        return result
    result.container_selector = matched_selector

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

    result.title = _text_of(root, detail.title_selectors) or _text_of(container, ["h1", "h2"])
    if not result.title:
        head_title = select_one(root, "title")
        result.title = " ".join(head_title.text_content().split()) if head_title else None
    result.agency = _strip_label(_text_of(root, detail.agency_selectors) or "") or None
    result.doc_no = _strip_label(_text_of(root, detail.doc_no_selectors) or "") or None
    result.date_text = _strip_label(_text_of(root, detail.date_selectors) or "") or None
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
            url = build_list_url(entry, page)
            if url is None:
                continue  # 该入口翻到底（等价于 gov.cn 越界页回空列表）
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
            # 容器全落空 = 解析失败。**不退化成整页文本**（那会把导航/脚本当正文入库）
            raise ValueError("；".join(detail.warnings) or "正文解析失败（容器选择器未命中）")
        return FulltextResult(
            body=detail.body,
            title=detail.title,
            issuer=detail.agency,
            doc_no=detail.doc_no,
            date=parse_date(detail.date_text),
            image_count=detail.image_count,
            attachments=[a.as_dict() for a in detail.attachments],
        )

    def fulltext_available(self) -> bool:
        # 配置化来源只需要 HTTP，不依赖 MCP 是否加载
        return True

    def normalize_item(self, raw: dict[str, Any]) -> PolicyItem | None:
        # 局部导入：fulltext_clean 经 schemas 反向依赖 sources，顶层导入会成环
        from app.pipeline.fulltext_clean import strip_zero_width

        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not title or not url:
            return None
        return PolicyItem(
            title=title,
            url=url,
            date=parse_date(raw.get("date")),
            agency=strip_zero_width(str(raw.get("agency") or "")).strip() or None,
            doc_no=strip_zero_width(str(raw.get("doc_no") or "")).strip() or None,
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
