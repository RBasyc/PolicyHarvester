"""抓取 + 提取流水线（Phase 4；Phase 9 在中间插入人工确认）。

分两段执行，中间由用户在页面上确认「要抓哪些篇目」：

    第一段（图 A，由 `POST /api/tasks` 触发）
    START → prepare → fetch ─┬─(有政策)→ await_confirm → END
                             └─(出错/0 结果)→ fail → END

    第二段（图 B，由 `POST /api/tasks/{id}/confirm` 触发，见 `start_extraction`）
    （丢弃未勾选篇目）→ extract → summarize → END

- `prepare`       任务置 running、写首条日志
- `fetch`         调用 MCP `get_latest_policies`（多关键词逐个检索后按 URL 去重）→ 落库 → 重算计数
- `await_confirm` 任务置 `awaiting_confirmation` 并停住——**图到此结束**，等用户确认
- `extract`       逐篇：`get_policy_fulltext` 取正文 → 大模型结构化提取 → 写 extraction + fact_item
- `summarize`     任务置 completed、汇总日志
- `fail`          任务置 failed，stage 里带出设计稿要求的中文提示

为什么拆成两段：图**没有 checkpointer**（`builder.compile()` 无 saver），`PipelineState` 只活在
一次 `ainvoke` 的内存里，没法「暂停后再恢复」。所以第一段跑完就结束，检索结果已落库
（`policy_document`）；用户确认后由 `run_extraction_phase()` 从库里重新读出篇目，**直接调用**
`extract_node` / `summarize_node` 跑完尾段——两个节点是纯函数式的 `dict → dict`，不依赖图的状态机。

设计要点：
- 由 `POST /api/tasks` / `POST /api/tasks/{id}/confirm` 以**后台 asyncio 任务**触发，不阻塞请求；
- 单篇失败只标记该篇 `failed` 并继续，不影响其他篇；
- 计数一律走 `refresh_task_counts()`（按实际行数派生），保证进度面板与结果表格同源一致；
- 工具按名字在运行时查找（`find_tool`），因此本图不依赖「先加载 MCP 再编译」的顺序。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.mcp import find_tool, tool_json
from app.config import get_settings
from app.db import repositories as repo
from app.db.repositories import PolicyRow
from app.pipeline.extract import extract_policy

logger = logging.getLogger(__name__)

MCP_SOURCE_LABEL = "China-Central-Policy-MCP"


class PipelineError(Exception):
    """流水线整体性失败（MCP 不可用、检索异常等）——stage 直接展示其文案。"""


class PipelineState(TypedDict, total=False):
    task_id: int
    source: str
    keywords: list[str]
    date_from: date | None
    date_to: date | None
    agencies: list[str]  # 发布机构分类（bmfl）
    doc_types: list[str]  # 文种（pcodeJiguan）
    # 本任务覆盖的流水线参数（页面「高级设置」；缺失/None = 用 .env 全局默认，见 _effective）
    pipeline_max_policies: int | None
    pipeline_per_keyword_limit: int | None
    pipeline_extract_concurrency: int | None
    pipeline_max_body_chars: int | None
    pipeline_mcp_timeout: int | None
    policies: list[dict[str, Any]]  # 待提取篇目（id / title / source_url）
    error: str | None


def _effective(source: dict[str, Any] | None, key: str) -> int:
    """读本任务覆盖的流水线参数；任务行 / 状态里没有（或为空）时回退 .env 全局默认。"""
    value = (source or {}).get(key)
    return int(value) if value else getattr(get_settings(), key)


# ---------- prepare ----------


async def prepare_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    stage = f"正在连接 {MCP_SOURCE_LABEL}（抓取源：{state.get('source', '')}）"
    await repo.set_task_running(task_id, stage)
    await repo.add_task_log(task_id, stage, tone="info")
    return {}


# ---------- fetch ----------


async def fetch_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    try:
        items = await _search_policies(
            state.get("keywords") or [],
            state.get("date_from"),
            state.get("date_to"),
            state.get("agencies") or [],
            state.get("doc_types") or [],
            overrides=state,
        )
    except PipelineError as exc:
        return {"error": str(exc)}

    rows = [row for item in items if (row := _to_policy_row(item, state.get("source")))]
    if not rows:
        return {
            "error": "未检索到匹配的政策原文，请调整关键词、时间范围或筛选条件后重试",
        }

    await repo.insert_policies(task_id, rows[: _effective(state, "pipeline_max_policies")])
    await repo.refresh_task_counts(task_id)

    # 以落库结果为准（去重/截断后真实入库的篇目）
    stored = await repo.fetch_task_policies(task_id)
    await repo.set_task_stage(task_id, f"已检索到 {len(stored)} 篇，等待确认抓取")
    await repo.add_task_log(task_id, f"检索完成：共获取 {len(stored)} 篇候选原文", tone="info")
    return {
        "policies": [
            {"id": p["id"], "title": p["title"], "source_url": p["source_url"]}
            for p in stored
        ],
        "error": None,
    }


# ---------- await_confirm ----------


async def await_confirm_node(state: PipelineState) -> dict:
    """停在「等用户确认」这一档：图到此结束，提取由确认接口另行触发。

    检索到的篇目此刻已经落在 `policy_document`（`fetch_node` 里写好的），
    因此即使用户不马上确认、甚至服务重启，候选列表也不会丢。
    """
    task_id = state["task_id"]
    count = len(state.get("policies") or [])
    stage = f"已检索到 {count} 篇，等待确认后开始提取"
    await repo.await_task_confirmation(task_id, stage)
    await repo.add_task_log(
        task_id,
        f"已检索到 {count} 篇，请在「检索结果确认」中勾选要抓取的篇目（不勾选的将被丢弃）",
        tone="active",
    )
    return {}


async def _search_policies(
    keywords: list[str],
    date_from: date | None,
    date_to: date | None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按「关键词 × 发布机构 × 文种」组合扇出检索并合并去重。

    gov.cn 检索 API 会静默忽略重复的筛选参数（`bmfl=A&bmfl=B` 等于不过滤），所以多选只能
    由服务端逐值扇出；组合数由 `pipeline_max_search_calls` 封顶，防止组合爆炸。
    每关键词条数 / 超时可用 `overrides`（任务行上的高级设置）按任务覆盖。
    """
    tool = find_tool("get_latest_policies")
    if tool is None:
        raise PipelineError(
            f"抓取失败：无法连接 {MCP_SOURCE_LABEL}（工具未加载），"
            "请确认本地服务已启动后重试"
        )

    settings = get_settings()
    per_keyword = _effective(overrides, "pipeline_per_keyword_limit")
    mcp_timeout = _effective(overrides, "pipeline_mcp_timeout")
    kw_list = keywords or [None]
    agency_list = agencies or [None]
    doc_type_list = doc_types or [None]

    combos = list(
        itertools.product(kw_list, agency_list, doc_type_list)
    )[: settings.pipeline_max_search_calls]

    facet_active = bool(agencies or doc_types)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword, agency, doc_type in combos:
        args: dict[str, Any] = {"limit": per_keyword}
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
            raw = await asyncio.wait_for(tool.ainvoke(args), timeout=mcp_timeout)
        except Exception as exc:  # noqa: BLE001 - 统一转成设计稿要求的提示文案
            raise PipelineError(
                f"抓取失败：连接 {MCP_SOURCE_LABEL} 超时或异常（{exc}），"
                "请确认本地服务已启动后重试"
            ) from exc

        try:
            data = tool_json(raw)
        except ValueError as exc:
            raise PipelineError(f"抓取失败：{exc}") from exc

        for item in (data or {}).get("items") or []:
            # ① 公报不参与 bmfl/pcodeJiguan 等筛选（实测恒 56 条纹丝不动）——筛选生效时整批丢弃。
            #    MCP 侧现在也会在「截断成 limit 条」之前先丢掉公报，这里保留作为兜底：万一线上
            #    跑的是未重建的旧 build/index.js，公报仍会混进来。
            catalog_key = str(item.get("catalog_key") or item.get("catalog") or "")
            if facet_active and catalog_key == "gongbao":
                continue
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
                continue
            # ③ 日期必须落在窗口内；窗口已设但日期缺失也丢弃（否则「无」不可信）。
            #    前提：MCP 报的 date 必须与接口做窗口过滤用的是同一个日期——即站点显示的发布日期
            #    （取 ptimeStr/pubtimeStr，文本缺失才落到 ptime 这个常为成文日期的 epoch）。若 MCP
            #    又改回优先取 epoch，这里会把接口按窗口返回的行整批误判成越界，出现「facet 有 1 条、
            #    实际 0 条」。
            item_date = _parse_date(item.get("date"))
            if date_from and (item_date is None or item_date < date_from):
                continue
            if date_to and (item_date is None or item_date > date_to):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(item)

    return merged


def _to_policy_row(item: dict[str, Any], source: str | None) -> PolicyRow | None:
    """MCP 检索结果 → `policy_document` 一行（标题/链接缺失的直接丢弃）。"""
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    if not title or not url:
        return None
    return {
        "title": title,
        "source_url": url,
        "publish_date": _parse_date(item.get("date")),
        "source_name": source,
        "agency": (str(item.get("issuing_agency") or "").strip() or None),
        "doc_no": (str(item.get("document_number") or "").strip() or None),
        "catalog": (str(item.get("catalog_key") or item.get("catalog") or "").strip() or None),
        "fetched_by": MCP_SOURCE_LABEL,
    }


def _parse_date(value: Any) -> date | None:
    """把 MCP 返回的 `2025-06-05` / `2025-6-5` 之类转成 date；解析不了就当没有。"""
    text = str(value or "").strip()
    match = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------- extract ----------


async def extract_node(state: PipelineState) -> dict:
    """逐篇提取。单篇的失败在 `_extract_one` 内部消化，这里只负责调度与并发上限。"""
    task_id = state["task_id"]
    policies = state.get("policies") or []
    total = len(policies)
    semaphore = asyncio.Semaphore(max(1, _effective(state, "pipeline_extract_concurrency")))

    async def run(index: int, policy: dict[str, Any]) -> None:
        async with semaphore:
            await _extract_one(task_id, index, total, policy, overrides=state)

    await asyncio.gather(*(run(i, p) for i, p in enumerate(policies, start=1)))
    return {}


async def _extract_one(
    task_id: int,
    index: int,
    total: int,
    policy: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> None:
    policy_id = policy["id"]
    title = policy["title"]
    url = policy.get("source_url")

    # 干净起点：清掉旧结果（重跑/重试时幂等）
    await repo.reset_extraction(policy_id)
    await repo.set_task_stage(
        task_id, f"正在调用大模型提取：第 {index}/{total} 篇 ——《{title}》"
    )
    await repo.add_task_log(task_id, f"正在提取第 {index} 篇 …", tone="active")

    try:
        fulltext = await _fetch_fulltext(url, policy_id, timeout=_effective(overrides, "pipeline_mcp_timeout"))
        body = str(fulltext.get("body") or "").strip()
        if len(body) < 50:
            raise ValueError("政策正文为空或过短，无法提取")

        payload, model = await extract_policy(
            title=str(fulltext.get("title") or title),
            body=body,
            url=url,
            max_body_chars=_effective(overrides, "pipeline_max_body_chars"),
        )
        await repo.save_extraction(
            policy_id,
            model=model,
            facts=[{"label": f.label, "content": f.content} for f in payload.facts],
            info_summary=payload.info.summary,
            # 落库形态与 knowledge_concepts 一致：[{"point": "..."}]（见 schema.sql）
            info_points=[{"point": p} for p in payload.info.points],
            knowledge_concepts=[
                {"term": c.term, "desc": c.desc} for c in payload.knowledge.concepts
            ],
            knowledge_fields=list(payload.knowledge.fields),
            knowledge_extension=payload.knowledge.extension,
        )
        await repo.add_task_log(
            task_id,
            f"第 {index} 篇提取成功（事实 {len(payload.facts)} · "
            f"信息 {len(payload.info.points)} · 知识 {len(payload.knowledge.concepts)}）",
            tone="success",
        )
    except Exception as exc:  # noqa: BLE001 - 单篇失败不能拖垮整个任务
        message = _short_error(exc)
        logger.warning("第 %d 篇《%s》提取失败：%s", index, title, exc)
        await repo.mark_extraction_failed(policy_id, message)
        await repo.add_task_log(task_id, f"第 {index} 篇提取失败：{message}", tone="info")
    finally:
        # 无论成败都重算计数，保证进度面板与结果表格始终一致
        await repo.refresh_task_counts(task_id)


async def _fetch_fulltext(url: str | None, policy_id: int, *, timeout: int | None = None) -> dict[str, Any]:
    """取政策全文，并用解析出的机构/文号回填 policy_document。"""
    if not url:
        raise ValueError("缺少原文链接，无法抓取全文")

    tool = find_tool("get_policy_fulltext")
    if tool is None:
        raise PipelineError(
            f"无法连接 {MCP_SOURCE_LABEL}（工具未加载），请确认本地服务已启动后重试"
        )

    try:
        raw = await asyncio.wait_for(
            tool.ainvoke({"url": url, "policy_id": str(policy_id)}),
            timeout=timeout if timeout else get_settings().pipeline_mcp_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"抓取全文失败（{exc}）") from exc

    data = tool_json(raw)
    if not isinstance(data, dict):
        raise ValueError("全文接口返回了非预期内容")

    await repo.update_policy_from_fulltext(
        policy_id,
        agency=str(data.get("issuer") or "").strip(),
        doc_no=str(data.get("doc_no") or "").strip(),
        publish_date=_parse_date(data.get("date")),
    )
    return data


def _short_error(exc: Exception) -> str:
    """错误文案压到一行一句话（前端日志与抽屉里直接展示）。"""
    text = " ".join(str(exc).split())
    return text[:180] or exc.__class__.__name__


# ---------- summarize / fail ----------


async def summarize_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    row = await repo.refresh_task_counts(task_id) or {}
    total = row.get("total_count", 0)
    extracted = row.get("extracted_count", 0)
    failed = row.get("failed_count", 0)
    await repo.finish_task(task_id, "completed", "已完成")
    await repo.add_task_log(
        task_id,
        f"任务完成：共 {total} 篇，提取成功 {extracted} 篇，失败 {failed} 篇",
        tone="success",
    )
    return {}


async def fail_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    error = state.get("error") or "抓取失败，请重试"
    await repo.refresh_task_counts(task_id)
    await repo.finish_task(task_id, "failed", error)
    await repo.add_task_log(task_id, error, tone="info")
    return {}


def _route_after_fetch(state: PipelineState) -> str:
    """检索到篇目 → 停下来等确认；出错或 0 结果 → 直接失败（0 结果即「近 N 天无相关政策」）。"""
    return "fail" if state.get("error") or not state.get("policies") else "await_confirm"


def build_pipeline_graph():
    """第一段：`prepare → fetch → (await_confirm | fail)`。

    注意这里**不包含** `extract` / `summarize`——人工确认把它们隔到了第二段
    （见 `run_extraction_phase`），两个节点函数仍在本模块，被直接调用。
    """
    builder = StateGraph(PipelineState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("fetch", fetch_node)
    builder.add_node("await_confirm", await_confirm_node)
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "fetch")
    builder.add_conditional_edges(
        "fetch", _route_after_fetch, {"await_confirm": "await_confirm", "fail": "fail"}
    )
    builder.add_edge("await_confirm", END)
    builder.add_edge("fail", END)
    return builder.compile()


# ---------- 后台执行器 ----------

_graph = None
# 在跑的后台任务，**按 task_id 归组**：既可能是整条流水线，也可能是单篇重跑。
# 终止 / 清空 / 关停都据此精确定位要取消哪些任务（见 cancel_pipeline）。
_running: dict[int, set[asyncio.Task]] = {}


def get_pipeline_graph():
    """进程内单例。工具是运行时按名字查找的，因此无需等待 MCP 加载完成即可编译。"""
    global _graph
    if _graph is None:
        _graph = build_pipeline_graph()
    return _graph


def _track(task_id: int, coro, name: str) -> asyncio.Task:
    """登记一个后台任务，结束后自动从 `_running` 摘除（空桶一并删掉）。"""
    task = asyncio.create_task(coro, name=name)
    _running.setdefault(task_id, set()).add(task)

    def _discard(finished: asyncio.Task) -> None:
        bucket = _running.get(task_id)
        if bucket is None:
            return
        bucket.discard(finished)
        if not bucket:
            _running.pop(task_id, None)

    task.add_done_callback(_discard)
    return task


def start_pipeline(task_id: int) -> None:
    """把流水线丢进后台 asyncio 任务（由 `POST /api/tasks` 调用），立即返回。"""
    _track(task_id, run_pipeline(task_id), f"pipeline-{task_id}")


def start_single_extraction(task_id: int, policy_id: int) -> None:
    """重新提取单篇（由重试 / 重新提取接口调用）。"""
    _track(task_id, run_single_extraction(task_id, policy_id), f"reextract-{policy_id}")


def start_extraction(task_id: int) -> None:
    """用户确认后跑第二段（由 `POST /api/tasks/{id}/confirm` 调用）。

    未勾选的篇目已被确认接口删掉，这里只需要把剩下的逐篇提取完。
    """
    _track(task_id, run_extraction_phase(task_id), f"extract-{task_id}")


async def cancel_pipeline(task_id: int) -> bool:
    """取消某个任务在跑的后台任务并等它们真正结束；返回是否确实取消了东西。

    调用方应在返回后再落终态：`run_pipeline` 的 `CancelledError` 分支写的是「服务重启…」，
    由调用方覆盖成终止文案（见 `POST /api/tasks/{id}/cancel`）。
    """
    tasks = list(_running.get(task_id, ()))
    for task in tasks:
        task.cancel()
    if tasks:
        # return_exceptions：被取消的任务会抛 CancelledError，这里只需等它们退出
        await asyncio.gather(*tasks, return_exceptions=True)
    return bool(tasks)


async def cancel_all_pipelines() -> int:
    """取消全部在跑的后台任务，返回取消掉的**任务数**（每个 task_id 计 1）。

    清空数据前必须先调用：否则流水线会继续往已删除的任务里写日志 / 计数。
    """
    cancelled = 0
    for task_id in list(_running):
        if await cancel_pipeline(task_id):
            cancelled += 1
    return cancelled


async def run_single_extraction(task_id: int, policy_id: int) -> None:
    """只处理一篇：状态置 running → 提取 → 按剩余篇目决定终态。

    任务只有在**没有** pending/extracting 的篇目时才回到 `completed`；
    否则保持 running 并在 stage 里说明还剩多少篇（seed 这种只提取了一部分的样本任务会走这一支）。
    """
    try:
        rows = await repo.fetch_task_policies(task_id)
        target = next((p for p in rows if p["id"] == policy_id), None)
        if target is None:
            logger.warning("重新提取失败：政策 %s 不属于任务 %s", policy_id, task_id)
            return

        # 覆盖参数跟着任务行走（单篇重跑也尊重创建任务时的高级设置）
        task_row = await repo.fetch_task(task_id) or {}
        await repo.set_task_running(task_id, f"正在重新提取：《{target['title']}》")
        total = len(rows)
        done = sum(
            1
            for p in rows
            if p["id"] != policy_id and p["status"] in ("extracted", "failed")
        )
        await _extract_one(
            task_id,
            done + 1,
            total,
            {"id": target["id"], "title": target["title"], "source_url": target["source_url"]},
            overrides=task_row,
        )

        await repo.refresh_task_counts(task_id)
        remaining = [
            p
            for p in await repo.fetch_task_policies(task_id)
            if p["status"] in ("pending", "extracting")
        ]
        if remaining:
            await repo.set_task_stage(
                task_id, f"《{target['title']}》处理完成，仍有 {len(remaining)} 篇待提取"
            )
        else:
            await repo.finish_task(task_id, "completed", "已完成")
            await repo.add_task_log(task_id, "全部篇目提取完成", tone="success")
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
        logger.exception("重新提取政策 %s 失败", policy_id)
        await repo.set_task_stage(task_id, f"重新提取失败：{_short_error(exc)}")


async def run_extraction_phase(task_id: int) -> None:
    """第二段：把任务里**剩下的**篇目逐篇提取完并汇总。

    进入时任务已被确认接口置为 `running`（`set_task_running`），因此这里从读库开始。
    `extract_node` / `summarize_node` 是纯函数式的 `dict → dict`（内部只写数据库、不改状态），
    所以不需要再包一层图，直接调用即可。
    """
    try:
        rows = await repo.fetch_task_policies(task_id)
        policies = [
            {"id": p["id"], "title": p["title"], "source_url": p["source_url"]}
            for p in rows
            if p["status"] in ("pending", "extracting")  # 只处理还没出结果的
        ]
        # 覆盖参数从任务行重读（确认与检索可能隔了很久，创建请求早已结束）
        task_row = await repo.fetch_task(task_id) or {}
        await extract_node({"task_id": task_id, "policies": policies, **{
            key: task_row.get(key)
            for key in (
                "pipeline_max_policies",
                "pipeline_per_keyword_limit",
                "pipeline_extract_concurrency",
                "pipeline_max_body_chars",
                "pipeline_mcp_timeout",
            )
        }})
        await summarize_node({"task_id": task_id})
    except asyncio.CancelledError:  # 终止 / 关停
        logger.info("任务 %s 的提取阶段被取消", task_id)
        await repo.finish_task(task_id, "failed", "服务重启导致任务中断，请重试")
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底，任务必须有终态
        logger.exception("任务 %s 的提取阶段异常退出", task_id)
        message = f"提取失败：{_short_error(exc)}"
        await repo.finish_task(task_id, "failed", message)
        await repo.add_task_log(task_id, message, tone="info")


async def run_pipeline(task_id: int) -> None:
    """执行一次流水线。任何未捕获异常都会把任务落到 failed 终态，避免前端一直转圈。"""
    task_row = await repo.fetch_task(task_id)
    if task_row is None:
        logger.error("流水线启动失败：任务 %s 不存在", task_id)
        return

    state: PipelineState = {
        "task_id": task_id,
        "source": task_row["source"],
        "keywords": list(task_row["keywords"] or []),
        "date_from": task_row["date_from"],
        "date_to": task_row["date_to"],
        # 用 .get 防御未迁移的老库（schema 迁移是先决条件）
        "agencies": list(task_row.get("agencies") or []),
        "doc_types": list(task_row.get("doc_types") or []),
        # 高级设置的本任务覆盖（老库没有这些列时为 None = 用 .env 默认）
        "pipeline_max_policies": task_row.get("pipeline_max_policies"),
        "pipeline_per_keyword_limit": task_row.get("pipeline_per_keyword_limit"),
        "pipeline_extract_concurrency": task_row.get("pipeline_extract_concurrency"),
        "pipeline_max_body_chars": task_row.get("pipeline_max_body_chars"),
        "pipeline_mcp_timeout": task_row.get("pipeline_mcp_timeout"),
    }
    try:
        await get_pipeline_graph().ainvoke(state)
    except asyncio.CancelledError:  # 服务关停
        logger.info("任务 %s 的流水线被取消", task_id)
        await repo.finish_task(task_id, "failed", "服务重启导致任务中断，请重试")
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底，任务必须有终态
        logger.exception("任务 %s 的流水线异常退出", task_id)
        message = f"抓取失败：{_short_error(exc)}"
        await repo.finish_task(task_id, "failed", message)
        await repo.add_task_log(task_id, message, tone="info")


async def shutdown_pipelines() -> None:
    """应用关停时取消在跑的流水线（由 lifespan 调用）。"""
    await cancel_all_pipelines()
