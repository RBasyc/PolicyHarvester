"""政策库检索工具（对话 Agent 的「先查库、再回答」入口）。

两个工具，一里一外：

- `search_policy_library`——查**库外**：系统里已启用的来源（内置 gov.cn + 接入过的
  地方/部门来源）现在有什么政策。三个动作：`sources`（列出系统里的来源与覆盖范围，
  含已登记但当前不可用的）、`search`（只读检索，不落库）、`fulltext`（取正文）。
- `search_library`——查**库内**：我们**已经抓进来并提取过**的成果（成果库）。
  两个动作：`list`（按关键词/机构/文种/年份查）、`detail`（读某篇的元信息 +
  提取结果三栏 + 归档正文）。

分成两个工具而不是一个，是因为两者回答的是不同问题：「外面有什么」与
「我们已经有了什么」。混在一起，模型会在库里明明有货时再去网上重抓一遍。

**为什么需要 `search_policy_library`**：Agent 原本能用的检索只有 MCP 的
`get_latest_policies` / `get_policy_facets`——**只覆盖国务院政策文件库（国家层面）**。于是用户问
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

_ACTIONS = ("sources", "search", "fulltext", "facets")

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
    - `facets`  某个来源的**可选筛选值**：发布机构、文种 + 每个值有多少条。
      口径与页面筛选器**完全一致**（gov.cn 走实时统计，其它来源给栏目名）。
      不知道能筛什么时先查这个，别猜机构名。

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

        if action == "facets":
            start, err = _parse_date(date_from, "date_from")
            if err:
                return _error(err)
            end, err = _parse_date(date_to, "date_to")
            if err:
                return _error(err)
            from app.services import facets as facet_svc

            facets = await facet_svc.get_facets(
                q=(keywords[0] if keywords else None),
                date_from=start,
                date_to=end,
                source=source,
            )
            return _dump(
                {
                    "ok": True,
                    "action": "facets",
                    "source": facets.source,
                    "source_label": facets.source_label,
                    "agencies": [{"name": item.name, "count": item.count} for item in facets.agencies],
                    "doc_types": [{"name": item.name, "count": item.count} for item in facets.doc_types],
                    "supports": facets.supports.model_dump(mode="json"),
                    "note": (
                        "这是内置静态名单（实时 facet 不可用，或该来源本身没有 facet 接口）；"
                        "配置化来源的「发布机构」其实是站点栏目名，文种只能从文号解析。"
                        if facets.source == "fallback"
                        else "实时统计的结果，条数随关键词与时间范围变化。"
                    ),
                }
            )

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


@tool
async def search_library(
    action: str = "list",
    keyword: str | None = None,
    agency: str | None = None,
    doc_type: str | None = None,
    year: int | None = None,
    policy_id: str | None = None,
    limit: int = 10,
) -> str:
    """查**成果库**：我们已经抓进来并提取过的政策（库里有什么）。

    与 `search_policy_library` 的分工：那个查**库外**（各来源现在的政策），
    这个查**库内**（我们已有的成果）。用户问「我们库里有哪些…」「那篇文件提取出了
    什么」「之前抓过的那份文件」时用这个——**别再去网上重抓一遍**。

    两个动作：

    - `list`   按关键词 / 机构 / 文种 / 年份查库（口径与页面「成果库」列表完全一致：
      只收录**提取成功**的篇目，按原文链接去重，按发布日期倒序）。返回每篇的标题、
      发文机关、文号、发布日期、来源站点、原文链接、摘要与事实/要点条数。
    - `detail` 读某篇的详情：元信息 + **提取结果三栏**（事实 / 信息 / 知识）+ 归档正文。

    Args:
        action: `list` / `detail` 之一。
        keyword: 关键词（标题/摘要包含匹配）。`list` 用。
        agency: 发文机关（精确匹配）。`list` 用。
        doc_type: 文种（如 国发 / 浙政发）。`list` 用。
        year: 年份。`list` 用。
        policy_id: 篇目 id（`list` 的返回里有；字符串数字也行）。`detail` 用。
        limit: 最多返回几条（1–50，默认 10）。`list` 用。

    Returns:
        JSON 文本。`list` 返回 `total` / `items[]`；`detail` 返回 `policy` /
        `extraction`（事实 facts、信息 info、知识 knowledge）/ `fulltext`（归档正文，
        过长截断并说明）。失败时 `ok=false` + 中文原因。
    """
    action = (action or "").strip().lower()
    if action not in ("list", "detail"):
        return _error(f"action 需要是 list / detail 之一，收到：{action or '（空）'}")

    try:
        from app.services import library_query as svc
    except Exception as exc:  # noqa: BLE001 - 依赖装不全时也要给模型一句人话
        return _error(f"成果库服务不可用：{exc}")

    try:
        if action == "detail":
            if policy_id is None:
                return _error("detail 需要 policy_id（list 的返回里有）")
            return _dump(await svc.get_library_detail(policy_id))
        capped = max(1, min(int(limit or 10), 50))
        return _dump(
            await svc.list_library(
                keyword=keyword,
                agency=agency,
                doc_type=doc_type,
                year=year,
                limit=capped,
            )
        )
    except svc.LibraryQueryError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底：工具绝不抛异常
        return _error(f"成果库查询失败：{exc}")


# 注册表：`app/agent/tools.py` 把它并进 TOOLS
LIBRARY_TOOLS = [search_policy_library, search_library]
