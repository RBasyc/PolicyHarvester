"""筛选器可选值端点（发布机构分类 / 文种）。

- `GET /api/filters/facets?q=&date_from=&date_to=&source=` 返回当前检索条件下的 facet 名单 + 条数。
- **实现不在本文件**，而在 `app/services/facets.py`：对话侧的
  `search_policy_library(action="facets")` 走同一段代码——两边的筛选口径必须一致，
  否则模型会给用户报出一套页面根本选不出来的机构名。路由只留 HTTP 语义。
- 优先走 gov.cn 实时 facet（MCP `get_policy_facets`）；**调用成功就如实返回**，即便名单为空
  （0 命中时 gov.cn 回 `code=1001`，facet 皆空 → 前端提示「没有相关内容」）。
- 只有 MCP 不可用 / 超时 / 解析失败才回退静态名单（`source="fallback"`），保证筛选器仍可用。
- **`source`（抓取源 label）给的是配置化站点来源时走另一条口径**：发布机构 = 该来源的
  栏目（入口）名，没有可筛的文种。响应里的 `supports` 告诉前端该隐藏哪些筛选项——
  不隐藏就是静默的空结果（详见 `FacetSupports` 的注释）。
- `supports` 还回答**筛选在哪一层生效**：配置化来源若有站内检索入口，关键词进服务端；
  没有则关键词/日期都只是「取回后在已翻到的列表页里过滤」，召回受翻页深度限制。
  口径见 `sources/config.capability_summary`，前端必须把这件事说出来。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.schemas.task import FacetList
from app.services.facets import get_facets

router = APIRouter(prefix="/api/filters", tags=["filters"])


@router.get("/facets", response_model=FacetList)
async def facets(
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> FacetList:
    """发布机构 / 文种的可选值（随关键词与日期范围实时统计，像官网的筛选面板）。

    `source` 省略或指向内置 gov.cn = 原有实时 facet 路径，行为不变。
    """
    return await get_facets(q=q, date_from=date_from, date_to=date_to, source=source)
