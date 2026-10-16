"""国务院政策文件库（gov.cn）抓取源：封装 China-Central-Policy-MCP 两个工具的约定。

从 pipeline/crawl_graph 原样迁出的 gov.cn 专属知识：
- `search_plan` 的 `itertools.product` 扇出（gov.cn 检索 API 会静默忽略重复的筛选参数，
  `bmfl=A&bmfl=B` 等于不过滤，所以多选只能由服务端逐值扇出）；
- `validate_item` 的三条过滤（公报不参与筛选 / 标题与关键词至少共享一字 / 日期窗口复验）；
- `normalize_item` 的字段映射与零宽字符清理（gov.cn 的机关/文号值首常带 U+200B）；
- `fetch_fulltext` 的参数约定（{url, policy_id}）与返回字段。
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import date
from typing import Any

from app.agent.mcp import find_tool, tool_json
from app.config import get_settings
from app.sources.base import FulltextResult, PolicyItem, PolicySource, SourceError
from app.sources.util import parse_date


class GovCnSource(PolicySource):
    key = "gov_cn"
    label = "国务院政策文件库（gov.cn）"
    # 文种白名单（gov.cn 检索 pcodeJiguan 参数的可选值）
    doc_types = ("国令", "国发", "国函", "国办发", "国办函")
    fetched_by = "China-Central-Policy-MCP"
    # 全文接口的 `date` 取的是详情页元信息表的「成文日期」（getPolicyFulltext.ts 的
    # 优先级：成文日期 → <time datetime> → .date → 正文首个日期），**不是**发布日期。
    # 列表/检索接口给的 `ptimeStr`/`pubtimeStr` 才是发布日期，那一轮已经写在
    # `publish_date` 上了，这里必须声明成 False，别让成文日期把它顶掉。
    fulltext_date_is_publish = False

    def search_plan(
        self,
        keywords: list[str],
        agencies: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[tuple[str | None, str | None, str | None]]:
        kw_list = keywords or [None]
        agency_list = agencies or [None]
        doc_type_list = doc_types or [None]
        return list(itertools.product(kw_list, agency_list, doc_type_list))

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
        tool = find_tool("get_latest_policies")
        if tool is None:
            raise SourceError(
                f"无法连接 {self.fetched_by}（工具未加载），请确认本地服务已启动后重试"
            )
        args: dict[str, Any] = {"limit": limit, "page": page}
        if date_from:
            args["startdate"] = date_from.isoformat()
        if date_to:
            args["enddate"] = date_to.isoformat()
        if keyword:
            args["keyword"] = keyword
        if agency:
            args["agency"] = agency
        if doc_type:
            args["doc_type"] = doc_type
        try:
            raw = await asyncio.wait_for(tool.ainvoke(args), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise SourceError(
                f"连接 {self.fetched_by} 超时或异常（{exc}），请确认本地服务已启动后重试"
            ) from exc
        try:
            data = tool_json(raw)
        except ValueError as exc:
            raise SourceError(str(exc)) from exc
        return (data or {}).get("items") or []

    def validate_item(
        self,
        item: dict[str, Any],
        *,
        keyword: str | None,
        date_from: date | None,
        date_to: date | None,
        facet_active: bool,
    ) -> bool:
        # ① 公报不参与 bmfl/pcodeJiguan 等筛选（实测恒 56 条纹丝不动）——筛选生效时整批丢弃。
        #    MCP 侧现在也会在「截断成 limit 条」之前先丢掉公报，这里保留作为兜底：万一线上
        #    跑的是未重建的旧 build/index.js，公报仍会混进来。
        catalog_key = str(item.get("catalog_key") or item.get("catalog") or "")
        if facet_active and catalog_key == "gongbao":
            return False
        # ② 接口在「严格条件 0 命中」时会静默退回全量浏览列表——与关键词毫无交集的直接丢弃
        #    （MCP 已在解析端剥离 <em> 高亮标签，这里拿到的 title 是纯文本）。
        #    注意 gov.cn 的 title 检索是**分词**的：q=水域 会命中「…流域防洪建设…」「…水污染防治…」
        #    这类标题（水域 → 水 + 域），若强求「标题包含完整关键词」会把接口的正常命中整批误杀，
        #    症状是「facet 明明有机构/条数、抓取却报未检索到」。所以这里只要求标题与关键词
        #    **至少共享一个字**：足以拦下整页无关的浏览噪声，又不会误杀分词命中。
        title = str(item.get("title") or "").strip()
        if (
            keyword
            and keyword not in title
            and not any(ch in title for ch in keyword)
        ):
            return False
        # ③ 日期必须落在窗口内；窗口已设但日期缺失也丢弃（否则「无」不可信）。
        #    前提：MCP 报的 date 必须与接口做窗口过滤用的是同一个日期——即站点显示的发布日期
        #    （取 ptimeStr/pubtimeStr，文本缺失才落到 ptime 这个常为成文日期的 epoch）。若 MCP
        #    又改回优先取 epoch，这里会把接口按窗口返回的行整批误判成越界，出现「facet 有 1 条、
        #    实际 0 条」。
        item_date = parse_date(item.get("date"))
        if date_from and (item_date is None or item_date < date_from):
            return False
        if date_to and (item_date is None or item_date > date_to):
            return False
        return True

    async def fetch_fulltext(
        self, url: str, policy_id: int | None = None, *, timeout: int | None = None
    ) -> FulltextResult:
        tool = find_tool("get_policy_fulltext")
        if tool is None:
            raise SourceError("工具未加载")
        args: dict[str, Any] = {"url": url}
        if policy_id is not None:
            args["policy_id"] = str(policy_id)
        raw = await asyncio.wait_for(
            tool.ainvoke(args),
            timeout=timeout if timeout else get_settings().pipeline_mcp_timeout,
        )
        data = tool_json(raw)
        if not isinstance(data, dict):
            raise ValueError("全文接口返回了非预期内容")
        # 未重建的旧 build 没有 image_count 字段，按 0 处理（句末信号照样拦得住壳）
        return FulltextResult(
            body=str(data.get("body") or ""),
            title=str(data.get("title") or "") or None,
            issuer=str(data.get("issuer") or "") or None,
            doc_no=str(data.get("doc_no") or "") or None,
            date=parse_date(data.get("date")),
            image_count=int(data.get("image_count") or 0),
        )

    def fulltext_available(self) -> bool:
        return find_tool("get_policy_fulltext") is not None

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
            # 机关/文号先剥零宽字符再 strip：gov.cn 的值首常带 U+200B（见 fulltext_clean._ZWSP_RE）
            agency=strip_zero_width(str(raw.get("issuing_agency") or "")).strip() or None,
            doc_no=strip_zero_width(str(raw.get("document_number") or "")).strip() or None,
            catalog_key=str(raw.get("catalog_key") or "").strip() or None,
            catalog=str(raw.get("catalog") or "").strip() or None,
            # 检索循环内判别写回的标注；未判别（服务停用）时为 None
            judge_tagged=raw.get("judge_tagged"),
            judge_prob=raw.get("judge_prob"),
        )
