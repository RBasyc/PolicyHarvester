"""筛选器可选值端点（发布机构分类 / 文种）。

- `GET /api/filters/facets?q=&date_from=&date_to=&source=` 返回当前检索条件下的 facet 名单 + 条数。
- 优先走 gov.cn 实时 facet（MCP `get_policy_facets`）；**调用成功就如实返回**，即便名单为空
  （0 命中时 gov.cn 回 `code=1001`，facet 皆空 → 前端提示「没有相关内容」）。
- 只有 MCP 不可用 / 超时 / 解析失败才回退静态名单（`source="fallback"`），保证筛选器仍可用。
- **`source`（抓取源 label）给的是配置化站点来源时走另一条口径**：发布机构 = 该来源的
  栏目（入口）名，没有可筛的文种。响应里的 `supports` 告诉前端该隐藏哪些筛选项——
  不隐藏就是静默的空结果（详见 `FacetSupports` 的注释）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter

from app.agent.mcp import find_tool, tool_json
from app.config import get_settings
from app.data.agencies import FALLBACK_AGENCIES, FALLBACK_DOC_TYPES
from app.schemas.task import FacetList, FacetOption, FacetSupports
from app.sources.registry import get_by_label, get_default

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/filters", tags=["filters"])


def _configured_source(label: str | None):
    """`label` 指向配置化站点来源时返回它的适配器；内置 gov.cn / 未指定 / 认不出 → None。

    `get_by_label` 对认不出的名字会**回退到内置源**（流水线也是这个口径），
    所以这里只需要判断「回退后的适配器是不是内置的那个」。
    """
    if not label:
        return None
    resolved = get_by_label(label)
    if resolved is None or resolved.key == get_default().key:
        return None
    return resolved if getattr(resolved, "config", None) is not None else None


def _configured_facets(source) -> FacetList:
    """配置化来源的筛选项：发布机构 = 入口（栏目）名，且**没有**可筛的文种。

    `EntrypointConfig.name` 同时是「发布机构」的匹配值（见 `sources/config.py`），
    前端把入口名当机构展示，用户选哪个就只抓那个栏目——口径正好对得上。
    文种在配置化来源上只能从文号解析出来（`doc_type_patterns`），**不能作为筛选项**：
    栏目标列表页给不了文种，`HtmlListSource.search_plan` 会告警并忽略传入的文种。
    """
    entrypoints = [ep.name for ep in source.config.entrypoints if ep.name]
    return FacetList(
        agencies=[FacetOption(name=name, count=0) for name in entrypoints],
        doc_types=[],
        source="fallback",  # 不是 gov.cn 的实时统计：配置化来源没有 facet 接口可查
        supports=FacetSupports(
            agencies=bool(entrypoints),
            doc_types=False,
            note=(
                f"来源「{source.label}」是配置化站点来源：发布机构 = 站点栏目（入口）名；"
                "文种只能从文号解析，不能作为筛选项"
            ),
        ),
        source_label=source.label,
    )


@router.get("/facets", response_model=FacetList)
async def get_facets(
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> FacetList:
    """发布机构 / 文种的可选值（随关键词与日期范围实时统计，像官网的筛选面板）。

    `source` 省略或指向内置 gov.cn = 原有实时 facet 路径，行为不变。
    """
    configured = _configured_source(source)
    if configured is not None:
        return _configured_facets(configured)

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
                source_label=source or get_default().label,
            )

    return FacetList(
        agencies=[FacetOption(name=n, count=0) for n in FALLBACK_AGENCIES],
        doc_types=[FacetOption(name=n, count=0) for n in FALLBACK_DOC_TYPES],
        source="fallback",
        source_label=source or get_default().label,
    )
