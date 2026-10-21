"""抓取任务路由。

- GET  /api/tasks/current       最近一次任务（无任务返回 null → 前端空状态）
- GET  /api/tasks/{id}          单个任务
- GET  /api/tasks/{id}/logs     日志流
- GET  /api/tasks/{id}/stream   进度实时推送（SSE）
- GET    /api/tasks/{id}/policies 抓取结果列表 + 四态统计
- POST   /api/tasks               创建任务并后台启动「检索」段（Phase 4）
- POST   /api/tasks/{id}/confirm  确认要抓取的篇目 → 后台启动「提取」段（Phase 9）
- POST   /api/tasks/{id}/cancel   终止运行中的任务（取消后台流水线 → 落 failed 终态）
- DELETE /api/tasks/{id}          删除单个任务及其全部结果
- DELETE /api/tasks               清空全部任务与结果（页面回到空状态）

注意：`/current` 必须声明在 `/{task_id}` 之前，否则会被当作路径参数解析。

**创建与确认这两个动作的实现不在本文件**，而在 `app/services/ingestion.py`：对话侧的
`ingest_policies` 工具走同一条路（路由只做 HTTP 语义：状态码、响应模型、错误映射）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.db import repositories as repo
from app.pipeline.crawl_graph import cancel_all_pipelines, cancel_pipeline
from app.schemas.policy import PolicyResultsResponse, PolicyResultItem, PolicySummary
from app.schemas.task import (
    TaskConfirm,
    TaskCreate,
    TaskCreated,
    TaskDTO,
    TaskDeleted,
    TaskLogDTO,
)
from app.services import ingestion

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# SSE 轮询节奏：数据库快照 + 增量日志
_STREAM_POLL_SECONDS = 0.6
_STREAM_HEARTBEAT_SECONDS = 15.0
# 任务终态（SSE 判定用；取消动作的终态判定在 services/ingestion.py 里）
_TERMINAL_STATUSES = ("completed", "failed")


def _sse(payload: dict) -> str:
    """与 /api/chat/stream 同格式：一行 `data: {...}` + 空行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _get_task_or_404(task_id: int) -> dict:
    task = await repo.fetch_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return task


@router.get("/current", response_model=TaskDTO | None)
async def get_current_task() -> TaskDTO | None:
    """最近一次抓取任务；库里没有任何任务时返回 `null`。"""
    row = await repo.fetch_current_task()
    return TaskDTO.from_row(row) if row else None


@router.post("", response_model=TaskCreated, status_code=201)
async def create_task(payload: TaskCreate) -> TaskCreated:
    """创建抓取任务：落库（status=pending）+ 首条日志 → 后台启动流水线。

    流水线在独立 asyncio 任务中执行，本请求立即返回 `task_id`；
    进度通过 `GET /api/tasks/{id}/stream` 推送。

    实际动作在 `app/services/ingestion.py`——对话侧的 `ingest_policies` 工具走同一条路。
    """
    try:
        task_id = await ingestion.start_ingestion(
            source=payload.source,
            keywords=payload.keywords,
            date_from=payload.date_from,
            date_to=payload.date_to,
            agencies=payload.agencies,
            doc_types=payload.doc_types,
            # 「任意网站（无头浏览器）」来源的入口 / 目标；其它来源传了 URL 会被服务层拒绝
            browse_url=payload.browse_url,
            browse_goal=payload.browse_goal,
            # 高级设置的本任务覆盖（None 的键 = 用服务端 .env 默认）
            pipeline_overrides={
                "pipeline_max_policies": payload.pipeline_max_policies,
                "pipeline_per_keyword_limit": payload.pipeline_per_keyword_limit,
                "pipeline_extract_concurrency": payload.pipeline_extract_concurrency,
                "pipeline_max_body_chars": payload.pipeline_max_body_chars,
                "pipeline_mcp_timeout": payload.pipeline_mcp_timeout,
                "pipeline_model": payload.pipeline_model,
            },
        )
    except ingestion.IngestionRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskCreated(task_id=task_id)


@router.post("/{task_id}/confirm", response_model=TaskDTO)
async def confirm_task(task_id: int, payload: TaskConfirm) -> TaskDTO:
    """确认要抓取哪些篇目 → 丢弃未勾选的 → 后台启动提取段（Phase 9）。

    只有 `awaiting_confirmation`（检索完成、等确认）的任务可以确认，其余返回 `409`。
    未勾选的篇目**直接删行**（它们只是检索噪声，没有提取价值）；随后重算计数，
    否则进度面板的 `total_count` 会与实际篇目数对不上。

    实际动作在 `app/services/ingestion.py`（对话侧确认走它，且必须 `user_approved`）。
    """
    if not payload.policy_ids:
        raise HTTPException(status_code=400, detail="请至少勾选一篇要抓取的政策")

    try:
        # 页面这条入口不需要额外的「用户已同意」声明：勾选本身就是那个动作
        await ingestion.confirm_ingestion(task_id, payload.policy_ids, user_approved=True)
    except ingestion.IngestionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ingestion.IngestionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ingestion.IngestionRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskDTO.from_row(await _get_task_or_404(task_id))


@router.post("/{task_id}/cancel", response_model=TaskDTO)
async def cancel_task(task_id: int) -> TaskDTO:
    """终止运行中的任务。

    实现不在本文件，而在 `app/services/ingestion.py::cancel_ingestion`——对话侧的
    `ingest_policies(action="cancel")` 走同一段代码（取消后台 asyncio 流水线并等它退出，
    再把任务落 `failed` 终态）。顺序不能反：`run_pipeline` 的取消分支写的是
    「服务重启…」，由服务层覆盖成终止文案。

    已是终态的任务返回 `409`（前端据此提示「任务已结束」）。
    """
    try:
        await ingestion.cancel_ingestion(task_id)
    except ingestion.IngestionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ingestion.IngestionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TaskDTO.from_row(await _get_task_or_404(task_id))


@router.delete("/{task_id}", response_model=TaskDeleted)
async def delete_task(task_id: int) -> TaskDeleted:
    """删除单个任务及其全部结果（政策 / 提取 / 事实 / 日志随外键级联删除）。"""
    await cancel_pipeline(task_id)  # 先停掉后台写入，再删行
    if not await repo.delete_task(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return TaskDeleted(deleted=1)


@router.delete("", response_model=TaskDeleted)
async def clear_tasks() -> TaskDeleted:
    """清空全部任务与结果（页面回到「尚未抓取」）。

    必须先取消在跑的后台任务：否则它们会继续往已删除的任务里写日志与计数。
    """
    await cancel_all_pipelines()
    return TaskDeleted(deleted=await repo.delete_all_tasks())


@router.get("/{task_id}", response_model=TaskDTO)
async def get_task(task_id: int) -> TaskDTO:
    return TaskDTO.from_row(await _get_task_or_404(task_id))


@router.get("/{task_id}/logs", response_model=list[TaskLogDTO])
async def get_task_logs(task_id: int) -> list[TaskLogDTO]:
    await _get_task_or_404(task_id)
    rows = await repo.fetch_task_logs(task_id)
    return [TaskLogDTO.from_row(r) for r in rows]


@router.get("/{task_id}/policies", response_model=PolicyResultsResponse)
async def get_task_policies(task_id: int) -> PolicyResultsResponse:
    """「抓取结果」表格：行数据 + 顶部四态统计。"""
    await _get_task_or_404(task_id)
    rows = await repo.fetch_task_policies(task_id)
    items = [PolicyResultItem.from_row(r) for r in rows]
    return PolicyResultsResponse(items=items, summary=PolicySummary.from_items(items))


@router.get("/{task_id}/stream")
async def stream_task(task_id: int) -> StreamingResponse:
    """任务进度 SSE。

    事件（每条为 `data: {...}\\n\\n`）：
    - `task`：任务全量快照（含 `stage` / `progress` / `eta_seconds` / 计数）
    - `log` ：单条新日志（`{time, tone, message}`）
    - `done`：任务进入终态（completed / failed），随后连接关闭
    - `error`：读取过程中出错

    订阅时会先补发**全部历史日志**（从 id 最小开始），因此前端可以只依赖本接口；
    之后只推增量。若任务已是终态，首轮即发 `task` + `done` 后关闭。
    """
    await _get_task_or_404(task_id)

    async def event_source() -> AsyncGenerator[str, None]:
        last_log_id = 0
        last_snapshot: str | None = None
        idle_seconds = 0.0
        try:
            while True:
                row = await repo.fetch_task(task_id)
                if row is None:
                    yield _sse({"type": "error", "message": f"任务不存在：{task_id}"})
                    return

                task = TaskDTO.from_row(row)
                snapshot = json.dumps(
                    task.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
                )
                if snapshot != last_snapshot:
                    last_snapshot = snapshot
                    yield _sse({"type": "task", "task": json.loads(snapshot)})

                for log in await repo.fetch_logs_since(task_id, last_log_id):
                    last_log_id = log["id"]
                    yield _sse(
                        {"type": "log", "log": TaskLogDTO.from_row(log).model_dump()}
                    )

                if task.status in _TERMINAL_STATUSES:
                    yield _sse(
                        {
                            "type": "done",
                            "status": task.status,
                            "task": json.loads(snapshot),
                        }
                    )
                    return

                await asyncio.sleep(_STREAM_POLL_SECONDS)
                idle_seconds += _STREAM_POLL_SECONDS
                if idle_seconds >= _STREAM_HEARTBEAT_SECONDS:
                    idle_seconds = 0.0
                    yield ": ping\n\n"  # SSE 注释行，保活
        except asyncio.CancelledError:  # 客户端断开
            raise
        except Exception as exc:  # noqa: BLE001 - 流内错误以事件形式下发
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，日志才能逐条到达
        },
    )
