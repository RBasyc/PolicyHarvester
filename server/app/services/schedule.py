"""定时入库任务的服务层：页面与对话共用一份。

页面入口 `app/api/routes/schedules.py` 转调这里；对话侧用工具 `manage_schedule`。
**一处实现两个出入口**——启停、立即触发、删除的副作用都只在这里发生一次，
路由与工具只做各自的参数翻译与错误映射。

**要当心的一件事（既有设计，不是这里新加的）**：调度器触发一轮后会**自动确认并入库**
（`app/scheduler.py`），**绕过**对话侧那道人工确认闸。对话侧的工具说明必须写清这一点——
用户在对话里说「每天盯着 X」时，他授权的是「每天自动抓」，不是「每天自动花提取的钱」。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from app.db import repositories as repo
from app.schemas.schedule import ScheduleCreate, ScheduleJobDTO
from app.scheduler import (
    describe_schedule,
    is_schedule_job_running,
    next_run_of,
    trigger_schedule_job,
)


class ScheduleError(RuntimeError):
    """定时任务动作失败的基类。"""


class ScheduleNotFound(ScheduleError):
    """定时任务不存在。"""


class ScheduleConflict(ScheduleError):
    """状态不允许（已在跑、参数与状态冲突）。"""


def _to_time(value: str | None) -> time | None:
    """「"HH:MM"」→ time（`ScheduleCreate` 已校验格式，这里只做类型转换）。"""
    if value is None:
        return None
    return time.fromisoformat(value)


async def _job_or_404(job_id: int) -> dict[str, Any]:
    row = await repo.fetch_schedule_job(job_id)
    if row is None:
        raise ScheduleNotFound(f"定时任务不存在：{job_id}")
    return row


async def list_jobs() -> list[dict[str, Any]]:
    """定时任务列表（最新在前由 repository 保证）。"""
    rows = await repo.fetch_schedule_jobs()
    return [ScheduleJobDTO.from_row(row).model_dump(mode="json") for row in rows]


async def create_job(payload: ScheduleCreate) -> dict[str, Any]:
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
            "pipeline_model": payload.pipeline_model,
        },
        schedule_kind=payload.schedule_kind,
        interval_minutes=payload.interval_minutes,
        run_time=_to_time(payload.run_time),
        run_weekday=payload.run_weekday,
        next_run_at=None,  # 由下方统一按当前时刻排首次触发（与启停同一口径）
    )
    job = await _job_or_404(job_id)
    await repo.set_schedule_job_next_run(job_id, next_run_of(job, after=now))
    row = await _job_or_404(job_id)
    return ScheduleJobDTO.from_row(row).model_dump(mode="json")


async def toggle_job(job_id: int) -> dict[str, Any]:
    """启用 / 停用切换。停用清空 next_run_at（NULL = 不排程）；启用按当前时刻排下一个触发点。"""
    job = await _job_or_404(job_id)
    enabled = not job["enabled"]
    next_run = next_run_of(job, after=datetime.now().astimezone()) if enabled else None
    row = await repo.toggle_schedule_job(job_id, enabled, next_run)
    return ScheduleJobDTO.from_row(row).model_dump(mode="json")


async def run_job(job_id: int) -> dict[str, Any]:
    """立即触发一轮（后台执行，不等下一个触发点）。进度到「抓取进度」面板看。"""
    job = await _job_or_404(job_id)
    if is_schedule_job_running(job_id):
        # 防重入（Phase 13）：同一任务同一时刻只允许一轮——调度轮次正在跑时拒绝手动触发，
        # 否则会并发两轮（重复建任务、重复提取、游标双写）。
        raise ScheduleConflict(
            f"定时任务「{job['name']}」已有一轮在执行中，请等本轮结束后再试"
        )
    trigger_schedule_job(job_id)
    return {"job_id": job_id, "triggered": True}


async def delete_job(job_id: int) -> dict[str, Any]:
    """删除定时任务（已触发的抓取任务保留，可追溯）。"""
    if not await repo.delete_schedule_job(job_id):
        raise ScheduleNotFound(f"定时任务不存在：{job_id}")
    return {"deleted": 1}
