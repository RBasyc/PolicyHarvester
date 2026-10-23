"""跨来源的**只读**检索：对话 Agent 先问「系统里已启用的来源覆盖了什么」，再动手。

背景（用户 2026-10-21 提出的调用逻辑问题）：问「浙江省关于科技的政策法规库」时，
Agent 的旧做法是直接调 MCP 的 `get_latest_policies` / `get_policy_facets`——那两个工具
**只覆盖国务院政策文件库（国家层面）**，查不到就回答「浙江本省的不在这个库里」。
问题是它**从没问过系统里有没有浙江的来源**：接入工作流加进来的一手地方来源
（`content_source` 里 `status=active` 的配置化来源）明明就在进程内注册表里，
能检索、能取全文，只是没有一条**只读**的路给 Agent 用。

本模块补的正是这一段，三件事：

1. `list_sources()`——列出已启用的来源及其覆盖范围（层级 / 机构 / 栏目 / 检索能力）；
2. `search_sources()`——在这些来源上做**不落库、不计费**的关键词检索；
3. `fetch_source_fulltext()`——按 URL 取某篇正文（gov.cn 走 MCP，其余走来源适配器）。

与 `ingest_policies`（`app/services/ingestion.py`）的分工：那条路**会建任务**、
落 `policy_document`、停在人工确认闸，是「把文件抓进成果库」；本模块是
「看看有没有、有什么」——**不写任何表**，也不花提取的模型费。

**只用注册表里的来源**：进程内注册表（`app/sources/registry.py`）装载的是
`status=active` 的配置化来源 + 内置源，停用的来源不在其中——这就是「已启用」的
权威口径。数据库只用来补 `level` / `organization` 这类展示元信息，读不到时降级
（不影响检索本身）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.db import repositories as repo
from app.sources.base import FulltextResult, PolicySource, SourceError
from app.sources.browser_any import BROWSER_SOURCE_KEY
from app.sources.registry import all_sources
from app.sources.util import parse_date
from app.schemas.policy import FulltextMeta

logger = logging.getLogger(__name__)

# 内置源（gov.cn）不在 `content_source` 表里，靠 key 认。
_BUILTIN_KEY = "gov_cn"

_LEVEL_LABELS = {
    "national": "国家",
    "province": "省级",
    "city": "市级",
    "county": "县级",
    "other": "其它",
}

# 一个来源最多试几组「关键词 × 筛选」组合（gov.cn 的多选扇出会成倍增长）
_MAX_COMBOS = 6
# 单个来源最多返回多少条
_MAX_ITEMS_PER_SOURCE = 20
# 正文给模型看多少字（超了截断并说明；模型要的是结论，不是整篇法规）
_FULLTEXT_CHARS = 4000
# 附件清单最多回几条（name / ext / url）
_MAX_ATTACHMENTS = 10


class LibrarySearchError(RuntimeError):
    """请求本身不成立（未知来源、参数反了）；不是站点故障，不该重试。"""


@dataclass
class SourceInfo:
    """一个来源的视图：适配器 + 覆盖范围元信息。

    `loaded=False` 表示**库里有登记、但没装进进程内注册表**（启动时配置解析失败，
    或它是停用来源）——它照样要出现在清单里：用户问「浙江」而浙江的来源没装载，
    Agent 必须知道「有但用不了」，而不是当作「没有这个来源」。
    """

    source: PolicySource | None
    key: str
    label: str
    level: str = "other"
    organization: str | None = None
    builtin: bool = False
    searchable: bool = True
    loaded: bool = True
    status: str | None = None
    entrypoints: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)

    @property
    def level_label(self) -> str:
        return _LEVEL_LABELS.get(self.level, self.level)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "level": self.level,
            "level_label": self.level_label,
            "organization": self.organization,
            "builtin": self.builtin,
            "loaded": self.loaded,
            "status": self.status,
            "searchable": self.searchable and self.loaded,
            "entrypoints": list(self.entrypoints),
            "domains": list(self.domains),
            "filters": dict(self.filters),
        }

    def matches(self, terms: list[str]) -> bool:
        """是否命中某个线索词（地域 / 机构 / 层级）。任一命中即可。"""
        if not terms:
            return True
        haystack = " ".join(
            [
                self.label,
                self.key,
                self.organization or "",
                self.level,
                self.level_label,
                " ".join(self.domains),
            ]
        ).lower()
        return any(term in haystack for term in terms)


def _scope_terms(scope: str | None) -> list[str]:
    """把线索词拆成小写词表（支持「浙江, 科技」这种写法）。"""
    if not scope:
        return []
    raw = scope.replace("，", " ").replace(",", " ").replace("、", " ").replace(";", " ")
    return [term.strip().lower() for term in raw.split() if term.strip()]


async def _collect_sources() -> list[SourceInfo]:
    """进程内注册表里的已启用来源（+ 数据库里已登记但没装载的行）。"""
    db_rows: dict[str, dict[str, Any]] = {}
    try:
        for row in await repo.list_content_sources():
            db_rows[str(row.get("key"))] = row
    except Exception as exc:  # noqa: BLE001 - 读不到元数据不该让检索整体失败
        logger.warning("读取来源元数据失败（按无元数据处理）：%s", exc)

    infos: list[SourceInfo] = []
    seen_keys: set[str] = set()
    for source in all_sources():
        if source.key == BROWSER_SOURCE_KEY:
            # 遗留伪来源「任意网站（无头浏览器）」：只为存量任务行保留，
            # 不出现在任何清单里（与 routes/sources.list_sources 同口径）。
            continue
        seen_keys.add(source.key)
        config = getattr(source, "config", None)
        row = db_rows.get(source.key) or {}
        entrypoints = [ep.name for ep in (getattr(config, "entrypoints", None) or [])]
        domains = list(getattr(getattr(config, "scope", None), "allowed_domains", None) or [])
        try:
            filters = dict(source.filter_capabilities() or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("来源「%s」读筛选能力失败：%s", source.label, exc)
            filters = {}
        try:
            # 无头浏览器来源的 search_plan 返回空（它按浏览目标采集，不做关键词检索）
            searchable = bool(source.search_plan(["__probe__"], [], []))
        except Exception:  # noqa: BLE001
            searchable = False
        infos.append(
            SourceInfo(
                source=source,
                key=source.key,
                label=source.label,
                level=str(getattr(config, "level", None) or row.get("level") or "other"),
                organization=getattr(config, "organization", None) or row.get("organization"),
                builtin=source.key == _BUILTIN_KEY,
                searchable=searchable,
                loaded=True,
                status=str(row.get("status") or "active"),
                entrypoints=entrypoints,
                domains=domains,
                filters=filters,
            )
        )

    # 库里有登记、但没装进注册表的行（停用 / 启动时装载失败）：照样列出来，
    # 让调用方能说清「有这个来源，但当前不可用」。
    for key, row in db_rows.items():
        if key in seen_keys:
            continue
        config = row.get("config")
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = {}
        if not isinstance(config, dict):
            config = {}
        infos.append(
            SourceInfo(
                source=None,
                key=key,
                label=str(row.get("label") or key),
                level=str(row.get("level") or "other"),
                organization=row.get("organization"),
                builtin=False,
                searchable=False,
                loaded=False,
                status=str(row.get("status") or "unknown"),
                entrypoints=[
                    str(entry.get("name"))
                    for entry in (config.get("entrypoints") or [])
                    if isinstance(entry, dict) and entry.get("name")
                ],
                domains=list((config.get("scope") or {}).get("allowed_domains") or []),
            )
        )
    return infos


def _resolve(infos: list[SourceInfo], raw: str) -> SourceInfo:
    """按 key / 展示名 / 名字片段解析来源；解析不出来就报错并列出可用清单。"""
    text = raw.strip()
    for info in infos:
        if info.key == text:
            return info
    exact = [info for info in infos if info.label == text]
    if len(exact) == 1:
        return exact[0]
    hits = [info for info in infos if text in info.label or text in info.key]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LibrarySearchError(
            f"未知来源「{text}」。当前可用："
            + "、".join(f"{info.label}（{info.key}）" for info in infos if info.loaded)
        )
    raise LibrarySearchError(
        f"来源名「{text}」匹配到多个："
        + "、".join(f"{info.label}（{info.key}）" for info in hits)
        + "——请用完整展示名或 key 指定"
    )


def _select(
    infos: list[SourceInfo], *, source: str | None, scope: str | None
) -> tuple[list[SourceInfo], list[SourceInfo]]:
    """选定要检索的来源：显式指定 > 线索词匹配 > 全部已启用来源。

    返回 `(可检索的, 命中线索但不可用的)`——第二份是「有登记但没装载」的来源，
    要如实告诉调用方，而不是当它不存在。
    """
    if source:
        info = _resolve(infos, source)
        if not info.loaded:
            raise LibrarySearchError(
                f"来源「{info.label}」已登记但当前不可用（状态：{info.status}，"
                "未装载进来源注册表），请在来源管理里启用它"
            )
        return [info], []
    terms = _scope_terms(scope)
    if not terms:
        return [info for info in infos if info.loaded], []
    matched = [info for info in infos if info.matches(terms)]
    return (
        [info for info in matched if info.loaded],
        [info for info in matched if not info.loaded],
    )


# ---------- 清单 ----------


async def list_sources(*, scope: str | None = None) -> dict[str, Any]:
    """列出已启用的来源与它们的覆盖范围（层级 / 机构 / 栏目 / 检索能力）。"""
    infos = await _collect_sources()
    terms = _scope_terms(scope)
    matched = [info for info in infos if info.matches(terms)] if terms else list(infos)
    payload: dict[str, Any] = {
        "ok": True,
        "scope": scope,
        "count": len(matched),
        "sources": [info.as_dict() for info in matched],
    }
    if terms and not matched:
        # 线索词没命中：把全部已启用来源摊开，让调用方能如实说出「系统里覆盖了哪些范围」
        payload["available"] = [info.as_dict() for info in infos]
        payload["note"] = (
            f"系统里没有已启用的来源命中「{scope}」；上面 available 是当前已启用的全部来源。"
            "没被覆盖的范围要说清楚，不要当成「不存在」。"
        )
    return payload


# ---------- 检索 ----------


def _item_view(item: Any, info: SourceInfo, in_library: set[str]) -> dict[str, Any]:
    """一条命中结果的精简视图。

    日期字段叫 `publish_date`：这里的日期来自**列表/检索轮**，那就是发布日期
    （gov.cn 的 `ptimeStr`/`pubtimeStr`，配置化来源的列表日期）。**全文轮**的日期
    另说——那是成文日期还是发布日期由来源声明，见 `fetch_source_fulltext`。
    """
    return {
        "title": item.title,
        "url": item.url,
        "publish_date": item.date.isoformat() if item.date else None,
        "agency": item.agency,
        "doc_no": item.doc_no,
        "catalog": item.catalog,
        "source": info.label,
        "source_key": info.key,
        "in_library": item.url in in_library,
    }


async def _search_one(
    info: SourceInfo,
    *,
    keywords: list[str],
    date_from: date | None,
    date_to: date | None,
    limit: int,
    in_library: set[str],
    budget: list[int],
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
) -> dict[str, Any]:
    """在单个来源上检索（不落库、不判别、不提取）。

    **预算与翻页口径与流水线同源**（Phase 33 G1）：
    - 每关键词条数取 `pipeline_per_keyword_limit`（与流水线同一个设置）；
    - 总调用数共享 `pipeline_max_search_calls` 预算（`budget` 是一个跨来源的计数器）；
    - 翻页：某组合这一页取不到**新**链接就停（与流水线「整轮零收获即停」同款，
      挡住「站点忽略页码、把第一页反复吐回来」的死循环）。
    """
    source = info.source
    if source is None:
        return {
            "key": info.key,
            "label": info.label,
            "level": info.level,
            "organization": info.organization,
            "count": 0,
            "items": [],
            "error": "该来源未装载进来源注册表（当前不可用），请先在来源管理里启用它",
        }
    if not info.searchable:
        return {
            "key": info.key,
            "label": info.label,
            "level": info.level,
            "organization": info.organization,
            "count": 0,
            "items": [],
            "error": "该来源不支持按关键词检索（无头浏览器来源按浏览目标采集），改用浏览或抓取工具",
        }

    settings = get_settings()
    timeout = settings.pipeline_mcp_timeout
    # 每关键词条数：调用方要的条数与流水线每关键词上限，取小的那个
    per_keyword = max(1, min(int(limit or 10), int(settings.pipeline_per_keyword_limit or limit or 10)))
    cap = min(limit, _MAX_ITEMS_PER_SOURCE)
    try:
        # 机构 / 文种交给来源的检索入口做服务端筛选（gov.cn 的 bmfl / pcodeJiguan）；
        # 不支持这一层的来源会在自己的 search 里忽略，不会因此报错。
        combos = source.search_plan(keywords, agencies or [], doc_types or [])
    except Exception as exc:  # noqa: BLE001
        return {
            "key": info.key,
            "label": info.label,
            "level": info.level,
            "organization": info.organization,
            "count": 0,
            "items": [],
            "error": f"检索计划生成失败：{exc}",
        }

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for keyword, agency, doc_type in list(combos)[:_MAX_COMBOS]:
        if budget[0] <= 0:
            errors.append("检索预算已用尽（后续来源/组合没查）")
            break
        page = 1
        while True:
            if budget[0] <= 0:
                errors.append("检索预算已用尽（后续页/来源没查）")
                break
            try:
                raw_items = await source.search(
                    keyword=keyword,
                    agency=agency,
                    doc_type=doc_type,
                    page=page,
                    limit=per_keyword,
                    date_from=date_from,
                    date_to=date_to,
                    timeout=timeout,
                )
            except SourceError as exc:
                errors.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001 - 单个来源失败不该拖垮其它来源
                errors.append(f"检索异常：{exc}")
                break
            budget[0] -= 1
            if not raw_items:
                break  # 该组合翻到底
            added = 0
            for raw in raw_items:
                try:
                    keep = source.validate_item(
                        raw,
                        keyword=keyword,
                        date_from=date_from,
                        date_to=date_to,
                        facet_active=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    # 校验异常就放行（与流水线 fail-open 同向），但要留痕——否则「校验坏掉」
                    # 会被当成「一条都不合条件」
                    errors.append(f"条目校验异常（已放行）：{exc}")
                    keep = True
                if not keep:
                    continue
                try:
                    item = source.normalize_item(raw)
                except Exception as exc:  # noqa: BLE001
                    # **不能静默丢**：解析失败会被下游看成「没检索到」，那是谎报
                    errors.append(f"条目解析失败：{exc}")
                    item = None
                if item is None or not item.url:
                    continue
                if item.url in seen:
                    continue
                seen.add(item.url)
                items.append(_item_view(item, info, in_library))
                added += 1
                if len(items) >= cap:
                    break
            if len(items) >= cap:
                break
            if added == 0:
                # 这一页没有新链接 = 翻页没生效（站点忽略了页码参数），别在这儿死循环
                break
            page += 1

    return {
        "key": info.key,
        "label": info.label,
        "level": info.level,
        "organization": info.organization,
        "count": len(items),
        "items": items,
        "error": "；".join(errors) if errors else None,
    }


async def search_sources(
    *,
    keywords: list[str],
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    scope: str | None = None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """在已启用的来源上做只读关键词检索（不落库、不判别、不提取）。"""
    infos = await _collect_sources()
    selected, unavailable = _select(infos, source=source, scope=scope)
    terms = _scope_terms(scope)

    try:
        in_library = await repo.fetch_library_urls()
    except Exception as exc:  # noqa: BLE001 - 成果库查不到不影响检索本身
        logger.warning("读取成果库链接失败（in_library 标注缺失）：%s", exc)
        in_library = set()

    results: list[dict[str, Any]] = []
    # 总调用预算跨来源共享（与流水线的 `pipeline_max_search_calls` 同一个设置）
    budget = [int(get_settings().pipeline_max_search_calls or 12)]
    for info in selected:
        results.append(
            await _search_one(
                info,
                keywords=keywords,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                in_library=in_library,
                budget=budget,
                agencies=agencies,
                doc_types=doc_types,
            )
        )

    total = sum(item["count"] for item in results)
    payload: dict[str, Any] = {
        "ok": True,
        "keywords": list(keywords),
        "scope": scope,
        "source": source,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "total": total,
        "results": results,
    }
    if unavailable:
        # 有登记但不可用的来源：说清楚是「有但用不了」，不是「没有」
        payload["unavailable"] = [
            {"key": info.key, "label": info.label, "status": info.status}
            for info in unavailable
        ]
    if not selected:
        payload["total"] = 0
        payload["results"] = []
        if unavailable:
            payload["note"] = (
                f"命中「{scope}」的来源已登记但当前不可用（见 unavailable）："
                "请先在来源管理里启用它，再检索。"
            )
        else:
            payload["note"] = (
                f"系统里没有已启用的来源命中「{scope}」——"
                "本次没有任何来源可检索。若要覆盖该范围，需要接入对应站点（网站来源接入工具）。"
            )
        payload["available"] = [info.as_dict() for info in infos]
    elif total == 0:
        failed = [item for item in results if item.get("error")]
        if failed:
            payload["note"] = (
                "没有命中，而且有来源检索失败（原因见各来源的 error）——先看错误，"
                "别把它当成「该地没有这类政策」。"
            )
        else:
            payload["note"] = (
                "检索没有命中：可能是关键词太窄、时间窗口太窄，或该来源的列表页不支持关键词检索"
                "（只有站内检索入口才能按关键词收窄）。"
            )
    elif terms:
        payload["note"] = (
            "命中结果只来自已启用且覆盖该范围的来源；没被覆盖的范围不在本次结果里，"
            "回答时要讲清楚覆盖边界。"
        )
    # 两条**恒定**的口径说明（Phase 33 G2 / G3）：不管有没有命中都要说清，
    # 否则模型会把「没判别」「没解析到」读成「没有」「不存在」。
    payload["caveats"] = [
        "只读检索**不做相关性判别**（入库时才会跑 laya 判别与正文预检）——"
        "结果是候选，不是定论；用户要入库时用 ingest_policies。",
        "条目里的机构 / 文号来自**列表页解析**，没解析到就是空（不是「没有发文机关」）；"
        "要准确值用 action=fulltext 读详情页。",
    ]
    return payload


# ---------- 正文 ----------


def _source_for_url(infos: list[SourceInfo], url: str) -> SourceInfo | None:
    """按域名把 URL 归到某个来源（gov.cn 没有域名白名单，靠 URL 前缀认）。"""
    host = (urlsplit(url).hostname or "").lower()
    for info in infos:
        for domain in info.domains:
            domain = domain.lower()
            if host == domain or host.endswith("." + domain):
                return info
    for info in infos:
        # 内置 gov.cn 没有域名白名单（走 MCP），按主机名认：www.gov.cn / gov.cn 都算
        if info.builtin and (host == "gov.cn" or host.endswith(".gov.cn")):
            return info
    return None


def _resolve_fulltext_source(infos: list[SourceInfo], url: str, source: str | None) -> SourceInfo:
    """按 `source` 名或 URL 域名挑出正文来源；三个守卫任一不过就抛 `LibrarySearchError`。"""
    if source:
        info = _resolve(infos, source)
    else:
        info = _source_for_url(infos, url)
        if info is None:
            raise LibrarySearchError(
                f"无法判断这篇属于哪个来源：{url}。请用 source 参数指定来源名"
                "（可用清单见 action=sources）。"
            )
    if info.source is None:
        raise LibrarySearchError(
            f"来源「{info.label}」已登记但当前不可用（状态：{info.status}，未装载进来源注册表）"
        )
    if not info.source.fulltext_available():
        raise LibrarySearchError(f"来源「{info.label}」的全文能力当前不可用（工具未加载）")
    return info


async def _fetch_fulltext_result(info: SourceInfo, url: str) -> FulltextResult:
    """调用来源全文工具；两种异常统一包成 `LibrarySearchError`（工具报错原样透传）。"""
    try:
        return await info.source.fetch_fulltext(url, timeout=get_settings().pipeline_mcp_timeout)
    except SourceError as exc:
        raise LibrarySearchError(f"取正文失败：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LibrarySearchError(f"取正文失败：{exc}") from exc


def _fulltext_body_quality(
    result: FulltextResult, max_chars: int
) -> tuple[FulltextMeta, str, int, int, bool, str | None]:
    """清洗正文并按流水线阈值判质量。返回 `(meta, clean, cjk, sent_enders, body_ok, body_note)`。

    局部导入：`app.pipeline.fulltext_clean` 经 `app.schemas` 反向依赖 `app.sources`，
    顶层导入会成环（与 `services/ingestion.py` 延迟导入 crawl_graph 同款理由）。
    """
    from app.pipeline.fulltext_clean import body_metrics, split_fulltext, strip_zero_width

    meta, clean = split_fulltext(result.body or "")
    cjk, enders = body_metrics(clean)
    settings = get_settings()
    min_cjk = settings.pipeline_min_body_chars
    min_enders = settings.pipeline_min_sent_enders
    body_ok = not (
        (min_cjk > 0 and cjk < min_cjk) or (min_enders > 0 and enders < min_enders)
    )
    body_note = None
    if not body_ok:
        # 措辞与流水线一致（crawl_graph.py 的 judge_reason）
        body_note = "图片版（正文为页面壳）" if result.image_count else "正文过短（薄壳/附件版）"
    return meta, clean, cjk, enders, body_ok, body_note


def _fulltext_field_extracts(
    result: FulltextResult,
    meta: FulltextMeta,
    *,
    publish_from_fulltext: bool,
) -> tuple[str | None, str | None, date | None, date | None]:
    """机构 / 文号 / 发布与成文日期。返回 `(agency, doc_no, publish_date, signed_date)`。

    - 机构 / 文号按「结构解析 → 归档元信息」的顺序兜底（与流水线一致）；
    - 发布日期只认来源声明为「发布日期」的那一路；gov.cn 的全文日期是成文日期；
    - 成文日期：详情页字段规则 / 归档元信息 / 正文落款，任一命中即可（不顶替发布日期）。
    """
    from app.pipeline.fulltext_clean import strip_zero_width  # 局部导入避环，理由见 _fulltext_body_quality

    strip = strip_zero_width
    agency = strip(result.issuer or "").strip() or strip(meta.agency or "").strip() or None
    doc_no = strip(result.doc_no or "").strip() or strip(meta.doc_no or "").strip() or None
    publish_date = result.date if publish_from_fulltext else None
    signed_text = (result.metadata or {}).get("signed_date") or meta.signed_date
    signed = parse_date(signed_text) if signed_text else None
    return agency, doc_no, publish_date, signed


async def fetch_source_fulltext(
    url: str,
    *,
    source: str | None = None,
    max_chars: int = _FULLTEXT_CHARS,
) -> dict[str, Any]:
    """取一篇正文（gov.cn 走 MCP 全文工具，其余走来源适配器）；只读，不落库。

    **口径与流水线对齐**（权威版本是 `app/pipeline/crawl_graph.py::_preflight_bodies`，
    本函数照抄它的判定，理由见 `docs/plans/2026-10-21-agent-alignment.md` A1–A4）：

    - 正文先过 `split_fulltext`（提元信息表、去页头面包屑、剥零宽字符），
      模型拿到的是**政策正文**，不是归档原文；
    - `publish_date` **只在来源声明「全文日期 = 发布日期」时给**
      （`PolicySource.fulltext_date_is_publish`）。gov.cn 的全文接口给的是
      **成文日期**（`app/sources/gov_cn.py:30-34`）——把它当发布日期说出去是错的，
      所以那一路 `publish_date` 为 null，成文日期单独走 `signed_date`；
    - 机构 / 文号按「结构解析 → 归档元信息」的顺序兜底（与流水线一致）；
    - 正文质量按 `body_metrics` + 流水线阈值给 `body_ok`：图片版页面壳与薄壳通知
      在这里被标出来，而不是被模型当成政策正文念出去。
    """
    infos = await _collect_sources()
    info = _resolve_fulltext_source(infos, url, source)
    result = await _fetch_fulltext_result(info, url)

    meta, clean, cjk, enders, body_ok, body_note = _fulltext_body_quality(
        result, max_chars
    )
    agency, doc_no, publish_date, signed = _fulltext_field_extracts(
        result, meta, publish_from_fulltext=info.source.fulltext_date_is_publish
    )

    attachments = [
        {
            "name": str(entry.get("name") or "") or None,
            "ext": str(entry.get("ext") or "") or None,
            "url": str(entry.get("url") or "") or None,
        }
        for entry in (result.attachments or [])[:_MAX_ATTACHMENTS]
    ]
    truncated = len(clean) > max_chars
    return {
        "ok": True,
        "source": info.label,
        "source_key": info.key,
        "url": url,
        "title": result.title,
        "agency": agency,
        "doc_no": doc_no,
        "publish_date": publish_date.isoformat() if publish_date else None,
        "signed_date": signed.isoformat() if signed else None,
        "date_note": (
            None
            if info.source.fulltext_date_is_publish
            else "该来源的全文接口给的是**成文日期**，不是发布日期（发布日期请以检索轮的 publish_date 为准）"
        ),
        "body": clean[:max_chars],
        "body_chars": len(clean),
        "raw_chars": len(result.body or ""),
        "body_metrics": {"cjk": cjk, "sent_enders": enders},
        "body_ok": body_ok,
        "body_note": body_note,
        "image_count": result.image_count,
        "attachments": attachments,
        "attachments_count": len(result.attachments or []),
        "truncated": truncated,
        "note": "正文过长已截断" if truncated else None,
    }
