"""抓取 + 提取流水线图（Phase 4）。

    START → prepare → fetch ─┬─(有政策)→ extract → summarize → END
                             └─(出错/0 结果)→ fail → END

- `prepare`   任务置 running、写首条日志
- `fetch`     调用 MCP `get_latest_policies`（多关键词逐个检索后按 URL 去重）→ 落库 → 重算计数
- `extract`   逐篇：`get_policy_fulltext` 取正文 → 大模型结构化提取 → 写 extraction + fact_item
- `summarize` 任务置 completed、汇总日志
- `fail`      任务置 failed，stage 里带出设计稿要求的中文提示

设计要点：
- 由 `POST /api/tasks` 以**后台 asyncio 任务**触发（见 `start_pipeline`），不阻塞请求；
- 单篇失败只标记该篇 `failed` 并继续，不影响其他篇；
- 计数一律走 `refresh_task_counts()`（按实际行数派生），保证进度面板与结果表格同源一致；
- 工具按名字在运行时查找（`find_tool`），因此本图不依赖「先加载 MCP 再编译」的顺序。
"""

from __future__ import annotations

import asyncio
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
    policies: list[dict[str, Any]]  # 待提取篇目（id / title / source_url）
    error: str | None


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
        )
    except PipelineError as exc:
        return {"error": str(exc)}

    rows = [row for item in items if (row := _to_policy_row(item, state.get("source")))]
    if not rows:
        return {
            "error": "未检索到匹配的政策原文，请调整关键词或时间范围后重试",
        }

    await repo.insert_policies(task_id, rows[: get_settings().pipeline_max_policies])
    await repo.refresh_task_counts(task_id)

    # 以落库结果为准（去重/截断后真实入库的篇目）
    stored = await repo.fetch_task_policies(task_id)
    await repo.set_task_stage(task_id, f"已抓取 {len(stored)} 篇，开始结构化提取")
    await repo.add_task_log(task_id, f"抓取完成：共获取 {len(stored)} 篇政策原文", tone="info")
    return {
        "policies": [
            {"id": p["id"], "title": p["title"], "source_url": p["source_url"]}
            for p in stored
        ],
        "error": None,
    }


async def _search_policies(
    keywords: list[str], date_from: date | None, date_to: date | None
) -> list[dict[str, Any]]:
    """按关键词逐个检索并合并去重；无关键词时取最新政策列表。"""
    tool = find_tool("get_latest_policies")
    if tool is None:
        raise PipelineError(
            f"抓取失败：无法连接 {MCP_SOURCE_LABEL}（工具未加载），"
            "请确认本地服务已启动后重试"
        )

    settings = get_settings()
    base_args: dict[str, Any] = {"limit": settings.pipeline_per_keyword_limit}
    if date_from:
        base_args["startdate"] = date_from.isoformat()
    if date_to:
        base_args["enddate"] = date_to.isoformat()

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword in keywords or [None]:
        args = {**base_args, **({"keyword": keyword} if keyword else {})}
        try:
            raw = await asyncio.wait_for(
                tool.ainvoke(args), timeout=settings.pipeline_mcp_timeout
            )
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
    semaphore = asyncio.Semaphore(max(1, get_settings().pipeline_extract_concurrency))

    async def run(index: int, policy: dict[str, Any]) -> None:
        async with semaphore:
            await _extract_one(task_id, index, total, policy)

    await asyncio.gather(*(run(i, p) for i, p in enumerate(policies, start=1)))
    return {}


async def _extract_one(
    task_id: int, index: int, total: int, policy: dict[str, Any]
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
        fulltext = await _fetch_fulltext(url, policy_id)
        body = str(fulltext.get("body") or "").strip()
        if len(body) < 50:
            raise ValueError("政策正文为空或过短，无法提取")

        payload, model = await extract_policy(
            title=str(fulltext.get("title") or title), body=body, url=url
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


async def _fetch_fulltext(url: str | None, policy_id: int) -> dict[str, Any]:
    """取政策全文，并用解析出的机构/文号回填 policy_document。"""
    if not url:
        raise ValueError("缺少原文链接，无法抓取全文")

    tool = find_tool("get_policy_fulltext")
    if tool is None:
        raise PipelineError(
            f"无法连接 {MCP_SOURCE_LABEL}（工具未加载），请确认本地服务已启动后重试"
        )

    settings = get_settings()
    try:
        raw = await asyncio.wait_for(
            tool.ainvoke({"url": url, "policy_id": str(policy_id)}),
            timeout=settings.pipeline_mcp_timeout,
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
    return "fail" if state.get("error") or not state.get("policies") else "extract"


def build_pipeline_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("fetch", fetch_node)
    builder.add_node("extract", extract_node)
    builder.add_node("summarize", summarize_node)
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "fetch")
    builder.add_conditional_edges(
        "fetch", _route_after_fetch, {"extract": "extract", "fail": "fail"}
    )
    builder.add_edge("extract", "summarize")
    builder.add_edge("summarize", END)
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
