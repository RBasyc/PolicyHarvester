"""成果库的**只读**查询：让对话 Agent 能读「我们库里已经有什么」。

**为什么单独一个模块**：抓取入库那条路（`app/services/ingestion.py` → 抓取 → 入库）
与查库这条路是两件事——一个是往库里放，一个是把库里已有的读出来给模型看。
`app/services/library_search.py` 查的是**库外**（各来源的实时检索），本模块查的是
**库内**（已入库、已提取的成果）。模型侧是**两个工具**（`search_policy_library` /
`search_library`），不混：混了模型就会「明明库里已有还去网上重抓一遍」。

口径与页面完全一致——同一批 repository 函数、同一批 schema 转换
（`app/api/routes/policies.py` 就是它们的 HTTP 门）。**只读**：不建任务、不写任何表。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import repositories as repo
from app.schemas.policy import LibraryPolicyItem, build_extraction_response

logger = logging.getLogger(__name__)

# 正文给模型看多少字（库里的正文是归档全文，可能很长）
_BODY_CHARS = 4000


class LibraryQueryError(RuntimeError):
    """查库请求不成立（篇目不存在、参数不对）；不是系统故障。"""


async def list_library(
    *,
    keyword: str | None = None,
    agency: str | None = None,
    doc_type: str | None = None,
    year: int | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """查成果库列表：按关键词 / 机构 / 文种 / 年份过滤，按发布日期倒序。

    与页面「成果库」列表**同一套口径**（`repo.fetch_library_policies` +
    `count_library_policies`）：只收录**提取成功**的篇目，按 `source_url` 去重。
    """
    kw = (keyword or "").strip() or None
    filters = {
        "agency": (agency or "").strip() or None,
        "doc_type": (doc_type or "").strip() or None,
        "year": year,
        "keyword": kw,
    }
    capped = max(1, min(int(limit or 10), 50))
    total = await repo.count_library_policies(**filters)
    rows = await repo.fetch_library_policies(**filters, limit=capped, offset=max(0, int(offset or 0)))
    items = [
        LibraryPolicyItem.from_row(row).model_dump(mode="json") for row in rows
    ]
    return {
        "ok": True,
        "total": total,
        "count": len(items),
        "offset": max(0, int(offset or 0)),
        "limit": capped,
        "filters": {key: value for key, value in filters.items() if value},
        "items": items,
        "note": (
            "库内命中就是已经抓进来并提取过的篇目——要原文链接用 source_url，"
            "要条款/事实用 action=detail 读提取结果。"
            if items
            else "库里没有匹配的篇目（如实说「库里没有」，别去网上重抓来充数）。"
        ),
    }


async def get_library_detail(
    policy_id: int | str,
    *,
    body_chars: int = _BODY_CHARS,
) -> dict[str, Any]:
    """读库里某篇的详情：元信息 + 提取结果三栏（事实 / 信息 / 知识）+ 归档正文。

    与页面详情页 (`GET /api/policies/{id}`) **同一段实现**
    （`build_extraction_response` + `split_fulltext`）。
    """
    try:
        pid = int(policy_id)
    except (TypeError, ValueError):
        raise LibraryQueryError(f"policy_id 需要是整数，收到：{policy_id}") from None

    detail = await repo.fetch_extraction_detail(pid)
    if detail is None:
        raise LibraryQueryError(f"库里没有这篇（id={pid}）")

    base = build_extraction_response(detail)
    archived = (detail["policy"] or {}).get("fulltext")
    fulltext_meta = None
    fulltext_body = None
    if archived:
        # 局部导入：fulltext_clean 经 app.schemas 反向依赖 app.sources，顶层导入会成环
        from app.pipeline.fulltext_clean import split_fulltext

        fulltext_meta, fulltext_body = split_fulltext(archived)

    body = fulltext_body or ""
    return {
        "ok": True,
        "policy": base.policy.model_dump(mode="json"),
        "extraction": base.extraction.model_dump(mode="json"),
        "fulltext_meta": fulltext_meta.model_dump(mode="json") if fulltext_meta else None,
        "fulltext": body[:body_chars] if body else None,
        "fulltext_chars": len(body),
        "fulltext_truncated": len(body) > body_chars,
        "note": "正文过长已截断" if len(body) > body_chars else None,
    }
