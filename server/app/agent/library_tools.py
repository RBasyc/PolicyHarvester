"""政策库检索工具（对话 Agent 的「先查库、再回答」入口）。

一个工具，三个动作：

    sources   列出系统里**已启用**的来源与它们的覆盖范围（层级 / 机构 / 栏目 / 检索能力）
    search    在这些来源上做**只读**检索（不落库、不判别、不提取，不花模型费）
    fulltext  取某篇正文（gov.cn 走 MCP 全文工具，地方来源走来源适配器）

**为什么需要它**：Agent 原本能用的检索只有 MCP 的 `get_latest_policies` /
`get_policy_facets`——**只覆盖国务院政策文件库（国家层面）**。于是用户问
「浙江省关于科技的政策法规库」时，Agent 直接去查国家库、查不到就回答
「浙江本省的不在这个库里」——它**从没问过系统里有没有浙江的来源**。
接入工作流加进来的一手地方来源就在进程内注册表里，只是没有一条只读的路给模型用。

与 `ingest_policies` 的分工：那条路**会建任务**、落 `policy_document`、停在人工确认闸，
是「把文件抓进成果库」；本工具是「看看有没有、有什么」——**一个字节都不写库**。
用户说「帮我抓进库里」时用 `ingest_policies`，问「有哪些」时用本工具。

**工具不抛异常**（与另外两个工具模块同一硬约束）：任何失败都返回
`{"ok": false, "error": "<中文原因>"}`，让模型能读着改。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agent.tool_utils import dump as _dump
from app.agent.tool_utils import error as _error
from app.agent.tool_utils import parse_iso_date as _parse_date

_ACTIONS = ("sources", "search", "fulltext")

# 一次检索每个来源最多返回几条（模型要的是「够不够挑」）
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 20


@tool
async def search_policy_library(
    action: str = "search",
    keywords: list[str] | None = None,
    scope: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    url: str | None = None,
) -> str:
    """检索系统里的政策库：**先看有哪些已启用的来源，再在这些来源里找**。

    三个动作：

    - `sources`  列出系统里的来源：名称、层级（国家/省/市/县）、机构、栏目、
      域名、检索能力，以及 `loaded` / `status`（**已登记但当前不可用的来源也在列**——
      用户问到某个地方而它的来源没装载，你要知道是「有但用不了」，不是「没有」）。
      **用户问到某个地方或部门时，先用这个动作看覆盖范围。**
    - `search`   在选中的来源里做只读关键词检索（**不落库、不入库、不花提取的模型费**）。
      返回标题 / 发布日期（`publish_date`）/ 机构 / 文号 / 原文链接，并标注 `in_library`
      （是否已在成果库里）。
    - `fulltext` 取某篇正文（给出 `url`）。用于精读条款、核对原文。返回的正文已清洗
      （去掉元信息表与页头噪声），并给出 `publish_date` / `signed_date`（**成文日期单列**，
      别把它当发布日期）、`body_ok`（正文质量）与附件清单。

    **为什么先查来源**：`get_latest_policies` / `get_policy_facets` 这两个 MCP 工具
    **只覆盖国务院政策文件库（国家层面）**。用户问「浙江省的政策」时，直接拿它们去答，
    会得到「浙江本省的文件不在这个库里」这种**没问过系统里有没有浙江来源**的结论。
    系统里接入过的地方来源（省/市政府门户、主管部门）才是地方问题的正解。

    Args:
        action: `sources` / `search` / `fulltext` 之一。
        keywords: 检索关键词（可多个）。`search` 用。
        scope: 线索词（地域 / 机构 / 层级，如「浙江」「科技部」「省级」），用来在已启用的
            来源里挑出相关的那几个。`source` 给了就以它为准。
        source: 指定来源（展示名或 key，如「浙江省人民政府门户」）。名字对不上直接报错
            并列出可用清单，不会替你换个近似来源。
        date_from / date_to: 时间范围（YYYY-MM-DD），留空 = 不限。
        limit: 每个来源最多返回几条（1–20，默认 10）。
        url: `fulltext` 用：要取正文的原文链接。

    Returns:
        JSON 文本。`sources` 返回来源清单；`search` 返回按来源分组的命中结果；
        `fulltext` 返回标题 / 机构 / 文号 / **发布日期 `publish_date`** /
        **成文日期 `signed_date`** / 正文（过长会截断并说明）/ `body_ok` / 附件清单。
        失败时 `ok=false` + 中文原因。
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"action 需要是 {' / '.join(_ACTIONS)} 之一，收到：{action or '（空）'}")

    try:
        from app.services import library_search as svc
    except Exception as exc:  # noqa: BLE001 - 依赖装不全时也要给模型一句人话
        return _error(f"政策库检索服务不可用：{exc}")

    try:
        if action == "sources":
            return _dump(await svc.list_sources(scope=scope))

        if action == "fulltext":
            target = (url or "").strip()
            if not target:
                return _error("fulltext 需要 url（原文链接）")
            return _dump(await svc.fetch_source_fulltext(target, source=source))

        start, err = _parse_date(date_from, "date_from")
        if err:
            return _error(err)
        end, err = _parse_date(date_to, "date_to")
        if err:
            return _error(err)
        terms = [k.strip() for k in (keywords or []) if str(k or "").strip()]
        if not terms:
            return _error("search 需要至少一个关键词")
        capped = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
        return _dump(
            await svc.search_sources(
                keywords=terms,
                date_from=start,
                date_to=end,
                source=source,
                scope=scope,
                limit=capped,
            )
        )
    except svc.LibrarySearchError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底：工具绝不抛异常
        return _error(f"政策库检索失败：{exc}")


# 注册表：`app/agent/tools.py` 把它并进 TOOLS
LIBRARY_TOOLS = [search_policy_library]
