"""「任意网站（无头浏览器）」来源：内置适配器，只负责**规范化与取全文**。

检索不走本类的 `search`（浏览器任务在 `pipeline/crawl_graph.fetch_node` 里走发现循环），
所以 `search_plan` 返回空、`search` 直接报错——避免有人误把它接进按组合扇出的老路径。

- 第一阶段：`browser_discovery` 用编号快照收集候选（真实 href），判别/预检仍复用
  流水线原有逻辑（`_judge_and_preflight`）；
- 第二阶段：`fetch_fulltext` 用无头浏览器重新打开详情页，HTML 交给
  `html_source.parse_detail_page`（空选择器 → 结构兜底），与配置化来源同一条清洗链。

`filter_capabilities` 声称所有筛选都在**取回后**生效（"client"）：任意站点没有站内检索
契约，前端不能标成「服务端支持」。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.sources.base import FulltextResult, PolicyItem, PolicySource, SourceError
from app.sources.util import parse_date

# 注意：`app.browser.*` 一律**函数内局部导入**。本模块经 `sources.registry` 在
# `app.sources` 包初始化期间被导入，而 `app.browser.content` 又反向依赖
# `app.sources.html_source`；顶层导入会在「先 import app.browser.manager」的场景下
# 形成半初始化模块的 ImportError。

BROWSER_SOURCE_KEY = "browser_any"
BROWSER_SOURCE_LABEL = "任意网站（无头浏览器）"


def browser_source_available() -> bool:
    """来源下拉是否应出现这一项（开关 + playwright 包；不启动浏览器）。"""
    from app.browser import runtime as browser_runtime

    return browser_runtime.available()


class BrowserAnySource(PolicySource):
    key = BROWSER_SOURCE_KEY
    label = BROWSER_SOURCE_LABEL
    doc_types = ()
    fetched_by = "无头浏览器"
    # 详情页上取到的是页面标注的日期（通常就是发布日期）
    fulltext_date_is_publish = True

    def search_plan(
        self,
        keywords: list[str],
        agencies: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[tuple[str | None, str | None, str | None]]:
        return []

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
        raise SourceError("「任意网站」来源需要入口网址（browse_url），不支持按关键词检索")

    def validate_item(
        self,
        item: dict[str, Any],
        *,
        keyword: str | None,
        date_from: date | None,
        date_to: date | None,
        facet_active: bool,
    ) -> bool:
        if not str(item.get("title") or "").strip():
            return False
        if not str(item.get("url") or item.get("source_url") or "").strip():
            return False
        value = item.get("date")
        parsed = value if isinstance(value, date) else parse_date(value)
        if parsed is not None:
            if date_from and parsed < date_from:
                return False
            if date_to and parsed > date_to:
                return False
        return True

    async def fetch_fulltext(
        self, url: str, policy_id: int | None = None, *, timeout: int | None = None
    ) -> FulltextResult:
        from app.browser import manager as browser_manager
        from app.browser import runtime as browser_runtime
        from app.browser.actions import BrowserActionError
        from app.browser.content import extract as extract_page_content

        if not browser_runtime.available():
            raise SourceError(browser_runtime.unavailable_reason())
        try:
            page = await browser_manager.fetch_html(url, timeout=timeout)
        except BrowserActionError as exc:
            raise SourceError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 浏览器异常统一转 SourceError
            raise SourceError(f"无头浏览器取全文失败：{exc}") from exc

        data = extract_page_content(
            page.get("html"),
            url=str(page.get("url") or url),
            inner_text=str(page.get("inner_text") or ""),
        )
        body = str(data.get("body") or "").strip()
        if len(body) < 50:
            raise SourceError("无头浏览器未取到可用的正文（页面可能是空壳或需要登录）")
        return FulltextResult(
            body=body,
            title=data.get("title"),
            issuer=data.get("agency"),
            doc_no=data.get("doc_no"),
            date=parse_date(data.get("date")),
            image_count=int(data.get("image_count") or 0),
            attachments=list(data.get("attachments") or []),
            metadata=dict(data.get("metadata") or {}),
            page_text=data.get("page_text"),
        )

    def fulltext_available(self) -> bool:
        from app.browser import runtime as browser_runtime

        return browser_runtime.available()

    def filter_capabilities(self) -> dict[str, Any]:
        return {
            "keyword": "client",  # 发现循环按目标筛选，取回后不再服务端过滤
            "date": "client",
            "agency": "client",
            "doc_type": "client",
            "search_entrypoints": [],
        }

    def normalize_item(self, raw: dict[str, Any]) -> PolicyItem | None:
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or raw.get("source_url") or "").strip()
        if not title or not url:
            return None
        value = raw.get("date")
        parsed = value if isinstance(value, date) else parse_date(value)
        agency = raw.get("agency") or raw.get("issuing_agency")
        doc_no = raw.get("doc_no") or raw.get("document_number")
        metadata = raw.get("metadata")
        return PolicyItem(
            title=title,
            url=url,
            date=parsed,
            agency=str(agency).strip() if agency else None,
            doc_no=str(doc_no).strip() if doc_no else None,
            catalog_key=None,
            catalog=None,
            judge_tagged=raw.get("judge_tagged"),
            judge_prob=raw.get("judge_prob"),
            extra=dict(metadata) if isinstance(metadata, dict) else {},
        )
