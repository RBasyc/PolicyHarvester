"""抓取任务路由。

- GET  /api/tasks/current       最近一次任务（无任务返回 null → 前端空状态）
- GET  /api/tasks/{id}          单个任务
- GET  /api/tasks/{id}/logs     日志流
- GET  /api/tasks/{id}/stream   进度实时推送（SSE）
- GET  /api/tasks/{id}/policies 抓取结果列表 + 四态统计
- POST /api/tasks               创建任务并后台启动流水线（Phase 4）

注意：`/current` 必须声明在 `/{task_id}` 之前，否则会被当作路径参数解析。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.db import repositories as repo
from app.pipeline.crawl_graph import start_pipeline
from app.schemas.policy import PolicyResultsResponse, PolicyResultItem, PolicySummary
from app.schemas.task import TaskCreate, TaskCreated, TaskDTO, TaskLogDTO

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# SSE 轮询节奏：数据库快照 + 增量日志
_STREAM_POLL_SECONDS = 0.6
_STREAM_HEARTBEAT_SECONDS = 15.0
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
    """
    task_id = await repo.create_task(
        source=payload.source,
        content_type=payload.content_type,
        date_from=payload.date_from,
        date_to=payload.date_to,
        schedule=payload.schedule,
        keywords=payload.keywords,
    )
    keyword_hint = "、".join(payload.keywords) if payload.keywords else "未设置"
    await repo.add_task_log(
        task_id,
        f"已创建抓取任务（源：{payload.source}；关键词：{keyword_hint}）",
        tone="info",
    )
    start_pipeline(task_id)
    return TaskCreated(task_id=task_id)


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
