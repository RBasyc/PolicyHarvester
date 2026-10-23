"""Tavily 联网检索（**只产 URL，不做任何入库决策**）。

用途：用户说「帮我查一下清远市的政策」时，系统里可能没有覆盖该地域的来源。对话工具
`ingest_policies(web_search=True)` 先调本模块把「一句话」变成**官方政策页面的入口 URL**，
再把 URL 交给 `start_ingestion` 的无头浏览器入口（`browse_url`）——页面上的候选质量由
发现循环与入库判别把守，URL 本身的可达性由浏览器入口校验把守。本模块只负责「找到入口」。

设计边界（与仓库既有约定一致）：

- **薄客户端**：一个 endpoint、一次 POST、不加 SDK 依赖。返回 `{ok, url, official, ...}`
  而不是抛异常——「没配 key / 网络不通 / 0 结果」都是**人话错误**，由调用翻译成
  `IngestionRejected`（工具侧再翻成 `{"ok": false}` 给模型）。
- **official 排序**：`.gov.cn` 域名的结果永不排在非官方项后面——但 Tavily 索引国内政务
  页面的深度有限，官方项可能整个缺席；此时仍回「第一名」并标 `official=False`，让调用方
  （chat 提示词）提醒用户「这不是 .gov.cn 域名，候选可能偏窄」，而不是静默失败。
- key / 参数走 `Settings`（进程启动时读取），改 `.env` 需重启——与 `OPENAI_*` 同组。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _is_gov_domain(url: str) -> bool:
    """URL 主机是否在 `.gov.cn`（含政府各部门子域）。解析失败按非官方算。"""
    try:
        host = (httpx.URL(url).host or "").lower()
    except Exception:  # noqa: BLE001 - 坏 URL 只影响排序，不该炸掉检索
        return False
    return host == "gov.cn" or host.endswith(".gov.cn")


def _rank_key(entry: dict[str, Any]):
    """排序键：官方域名优先，其次 Tavily 自己的相关度分。"""
    try:
        score = float(entry.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return (1 if _is_gov_domain(str(entry.get("url") or "")) else 0, score)


async def find_entry_url(query: str, *, max_results: int | None = None) -> dict[str, Any]:
    """把一句话检索成官方政策入口 URL。

    返回 `{ok, url, title, official, alternatives}` 或 `{ok: False, error}`；
    **不抛异常**——没配 key、网络失败、0 结果都是错误字段，调用方据此给用户一句人话。

    `max_results` 不传用 `TAVILY_MAX_RESULTS`（默认 10够了：头部再挑官方域名）。
    `alternatives` 是按官方优先排出的前 5 个候选（`{url, title, official}`），给
    模型在入口页打不开时挑下一个用的，不含任何库写入。
    """
    settings = get_settings()
    api_key = (settings.tavily_api_key or "").strip()
    if not api_key:
        return {
            "ok": False,
            "error": (
                "未配置 TAVILY_API_KEY：请在仓库根 .env 里加一行"
                " TAVILY_API_KEY=（tavily.com 免费注册）后重启 server"
            ),
        }

    limit = int(max_results if max_results is not None else settings.tavily_max_results)
    limit = max(1, min(limit, 20))
    payload = {
        "query": query,
        "search_depth": "basic",  # 只要 URL，不做深度内容聚合
        "max_results": limit,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    try:
        async with httpx.AsyncClient(timeout=float(settings.tavily_timeout_seconds)) as client:
            response = await client.post(
                _TAVILY_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001 - 网络失败也是一句人话，不该炸任务
        logger.warning("Tavily 检索失败（query=%s）：%s", query, exc)
        return {"ok": False, "error": f"Tavily 检索失败：{exc}"}

    results = [item for item in (data.get("results") or []) if isinstance(item, dict)]
    ranked = sorted(results, key=_rank_key, reverse=True)
    if not ranked:
        return {"ok": False, "error": f"Tavily 没有返回结果（query={query}），试着换更明确的搜索词"}

    top = ranked[0]
    official = _is_gov_domain(str(top.get("url") or ""))
    return {
        "ok": True,
        "url": str(top.get("url") or "").strip(),
        "title": str(top.get("title") or "").strip(),
        "official": official,
        "alternatives": [
            {
                "url": str(item.get("url") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "official": _is_gov_domain(str(item.get("url") or "")),
            }
            for item in ranked[:5]
        ],
    }
