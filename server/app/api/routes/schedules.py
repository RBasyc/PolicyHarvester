"""定时入库任务路由（Phase 11）。

- GET    /api/schedules             定时任务列表（管理弹窗用）
- POST   /api/schedules             新建定时任务（抓取配置快照 + 调度参数）
- POST   /api/schedules/{id}/toggle 启用 / 停用（停用即不排程，配置保留）
- POST   /api/schedules/{id}/run    立即运行一轮（不等下一个触发点）
- DELETE /api/schedules/{id}        删除（已触发的抓取任务保留，可追溯）

**实现不在本文件**，而在 `app/services/schedule.py`：对话侧的 `manage_schedule` 工具
走同一段代码（一处实现两个出入口）。本文件只做参数校验与 DTO 转换。

触发逻辑本身在 `app/scheduler.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.schedule import (
    ScheduleCreate,
    ScheduleDeleted,
    ScheduleJobDTO,
    ScheduleToggled,
    ScheduleTriggered,
)
from app.services import schedule as schedule_service

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleJobDTO])
async def list_schedules() -> list[ScheduleJobDTO]:
    return [ScheduleJobDTO.model_validate(row) for row in await schedule_service.list_jobs()]


@router.post("", response_model=ScheduleJobDTO, status_code=201)
async def create_schedule(payload: ScheduleCreate) -> ScheduleJobDTO:
    """新建定时任务：抓取配置**快照**落库，并按调度参数排出首次触发时间。"""
    try:
        row = await schedule_service.create_job(payload)
    except ValueError as exc:  # ScheduleCreate 的字段校验（如无头浏览器来源）已在上游拦过
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScheduleJobDTO.model_validate(row)


@router.post("/{job_id}/toggle", response_model=ScheduleToggled)
async def toggle_schedule(job_id: int) -> ScheduleToggled:
    """启用 / 停用切换。停用清空 next_run_at（NULL = 不排程）；启用按当前时刻排下一个触发点。"""
    try:
        row = await schedule_service.toggle_job(job_id)
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ScheduleToggled(job=ScheduleJobDTO.model_validate(row))


@router.post("/{job_id}/run", response_model=ScheduleTriggered)
async def run_schedule(job_id: int) -> ScheduleTriggered:
    """立即触发一轮（后台执行，不等下一个触发点）。进度到「抓取进度」面板看。"""
    try:
        await schedule_service.run_job(job_id)
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except schedule_service.ScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ScheduleTriggered()


@router.delete("/{job_id}", response_model=ScheduleDeleted)
async def delete_schedule(job_id: int) -> ScheduleDeleted:
    try:
        result = await schedule_service.delete_job(job_id)
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ScheduleDeleted(deleted=result["deleted"])
