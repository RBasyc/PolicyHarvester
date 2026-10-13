"""定时入库任务路由（Phase 11）。

- GET    /api/schedules             定时任务列表（管理弹窗用）
- POST   /api/schedules             新建定时任务（抓取配置快照 + 调度参数）
- POST   /api/schedules/{id}/toggle 启用 / 停用（停用即不排程，配置保留）
- POST   /api/schedules/{id}/run    立即运行一轮（不等下一个触发点）
- DELETE /api/schedules/{id}        删除（已触发的抓取任务保留，可追溯）

触发逻辑本身在 `app/scheduler.py`；本文件只做参数校验与 DTO 转换。
"""

from __future__ import annotations

from datetime import datetime, time

from fastapi import APIRouter, HTTPException

from app.db import repositories as repo
from app.scheduler import describe_schedule, next_run_of, trigger_schedule_job
from app.schemas.schedule import (
    ScheduleCreate,
    ScheduleDeleted,
    ScheduleJobDTO,
    ScheduleToggled,
    ScheduleTriggered,
)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


async def _get_job_or_404(job_id: int) -> dict:
    job = await repo.fetch_schedule_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"定时任务不存在：{job_id}")
    return job


@router.get("", response_model=list[ScheduleJobDTO])
async def list_schedules() -> list[ScheduleJobDTO]:
    rows = await repo.fetch_schedule_jobs()
    return [ScheduleJobDTO.from_row(row) for row in rows]


@router.post("", response_model=ScheduleJobDTO, status_code=201)
async def create_schedule(payload: ScheduleCreate) -> ScheduleJobDTO:
    """新建定时任务：抓取配置**快照**落库，并按调度参数排出首次触发时间。"""
    now = datetime.now().astimezone()
    name = payload.name or (
        f"定时任务 · {describe_schedule(payload.schedule_kind, payload.interval_minutes, _to_time(payload.run_time), payload.run_weekday)}"
    )
    job_id = await repo.create_schedule_job(
        name=name,
        source=payload.source,
        date_from=payload.date_from,
        keywords=payload.keywords,
        agencies=payload.agencies,
        doc_types=payload.doc_types,
        pipeline_overrides={
            "pipeline_max_policies": payload.pipeline_max_policies,
            "pipeline_per_keyword_limit": payload.pipeline_per_keyword_limit,
            "pipeline_extract_concurrency": payload.pipeline_extract_concurrency,
            "pipeline_max_body_chars": payload.pipeline_max_body_chars,
            "pipeline_mcp_timeout": payload.pipeline_mcp_timeout,
        },
        schedule_kind=payload.schedule_kind,
        interval_minutes=payload.interval_minutes,
        run_time=_to_time(payload.run_time),
        run_weekday=payload.run_weekday,
        next_run_at=None,  # 由下方统一按当前时刻排首次触发（与启停同一口径）
    )
    job = await _get_job_or_404(job_id)
    await repo.set_schedule_job_next_run(job_id, next_run_of(job, after=now))
    return ScheduleJobDTO.from_row(await _get_job_or_404(job_id))


@router.post("/{job_id}/toggle", response_model=ScheduleToggled)
async def toggle_schedule(job_id: int) -> ScheduleToggled:
    """启用 / 停用切换。停用清空 next_run_at（NULL = 不排程）；启用按当前时刻排下一个触发点。"""
    job = await _get_job_or_404(job_id)
    enabled = not job["enabled"]
    next_run = next_run_of(job, after=datetime.now().astimezone()) if enabled else None
    row = await repo.toggle_schedule_job(job_id, enabled, next_run)
    return ScheduleToggled(job=ScheduleJobDTO.from_row(row))


@router.post("/{job_id}/run", response_model=ScheduleTriggered)
async def run_schedule(job_id: int) -> ScheduleTriggered:
    """立即触发一轮（后台执行，不等下一个触发点）。进度到「抓取进度」面板看。"""
    await _get_job_or_404(job_id)
    trigger_schedule_job(job_id)
    return ScheduleTriggered()


@router.delete("/{job_id}", response_model=ScheduleDeleted)
async def delete_schedule(job_id: int) -> ScheduleDeleted:
    if not await repo.delete_schedule_job(job_id):
        raise HTTPException(status_code=404, detail=f"定时任务不存在：{job_id}")
    return ScheduleDeleted(deleted=1)


def _to_time(value: str | None) -> time | None:
    """"HH:MM" → time（ScheduleCreate 已校验格式，这里只做类型转换）。"""
    if value is None:
        return None
    return time.fromisoformat(value)
