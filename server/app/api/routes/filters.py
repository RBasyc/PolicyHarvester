"""筛选器可选值端点（发布机构分类 / 文种）。

- `GET /api/filters/facets?q=&date_from=&date_to=` 返回当前检索条件下的 facet 名单 + 条数。
- 优先走 gov.cn 实时 facet（MCP `get_policy_facets`）；**调用成功就如实返回**，即便名单为空
  （0 命中时 gov.cn 回 `code=1001`，facet 皆空 → 前端提示「没有相关内容」）。
- 只有 MCP 不可用 / 超时 / 解析失败才回退静态名单（`source="fallback"`），保证筛选器仍可用。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter

from app.agent.mcp import find_tool, tool_json
from app.config import get_settings
from app.data.agencies import FALLBACK_AGENCIES, FALLBACK_DOC_TYPES
from app.schemas.task import FacetList, FacetOption

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/filters", tags=["filters"])


@router.get("/facets", response_model=FacetList)
async def get_facets(
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> FacetList:
    """发布机构 / 文种的可选值（随关键词与日期范围实时统计，像官网的筛选面板）。"""
    tool = find_tool("get_policy_facets")
    if tool is not None:
        args: dict = {}
        if q:
            args["keyword"] = q
        if date_from:
            args["startdate"] = date_from.isoformat()
        if date_to:
            args["enddate"] = date_to.isoformat()
        try:
            raw = await asyncio.wait_for(
                tool.ainvoke(args), timeout=get_settings().pipeline_mcp_timeout
            )
            data = tool_json(raw)
        except Exception:  # noqa: BLE001 - 实时 facet 失败时静默回退静态名单
            logger.warning("get_policy_facets 调用失败，回退静态筛选名单", exc_info=True)
            data = None

        if data is not None:
            # 调用本身成功就如实返回，**哪怕两个名单都为空**：gov.cn 对「0 命中」返回
            # code=1001（msg=抱歉，没有找到相关结果），此时 facetMap/groupMap 皆空。
            # 这里若退回内置名单，前端将永远显示不出「没有相关内容」——内置名单恒非空。
            return FacetList(
                agencies=[
                    FacetOption(name=str(a.get("name")), count=int(a.get("count") or 0))
                    for a in data.get("agencies") or []
                    if a.get("name")
                ],
                doc_types=[
                    FacetOption(name=str(d.get("name")), count=int(d.get("count") or 0))
                    for d in data.get("doc_types") or []
                    if d.get("name")
                ],
                source="live",
            )

    return FacetList(
        agencies=[FacetOption(name=n, count=0) for n in FALLBACK_AGENCIES],
        doc_types=[FacetOption(name=n, count=0) for n in FALLBACK_DOC_TYPES],
        source="fallback",
    )
