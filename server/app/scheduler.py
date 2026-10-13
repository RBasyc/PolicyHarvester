"""定时入库调度器（Phase 11）。

一个轻量的进程内调度循环：每 tick 扫一次 `schedule_job` 里到点的任务，串行触发。
任务定义与触发记录全部落库，因此**服务重启后由 lifespan 重新武装调度器即可恢复**。

触发语义（与手动抓取的区别）：
- **跳过人工确认**：检索结果**全部保留**，直接接提取段跑完全流程（用户已确认的选择）；
- **跨轮 / 全库 URL 去重**：本任务历史轮次 ∪ 成果库（其它任务已提取成功）的 `source_url`
  直接跳过，本轮剔完为空记 `noop`（正常跳过，不是失败，也不会在页面上留下一条「未检索到」的失败任务）；
- **入库判别**（Phase 13）：检索循环内逐页判别（见 `_search_policies`），被标注「不建议入库」
  的篇目（批复/函等事务性文件）直接剔除、且不占抓取上限——抓满 cap 篇指**建议入库**的；
  判别服务不可达 / 超时 fail-open 全部保留，只告警；
- **滑动游标**（解决「固定窗口 + 上限 N + 最新优先 = 永远只抓到最新的 N 篇」）：
  `cursor_before` 是下一轮窗口的上界（exclusive）。
    - NULL    → 窗口 [date_from, 今天]，追最新发文；
    - 非 NULL → 窗口 [date_from, cursor_before - 1 天]，向更早回填。
  本轮**抓满上限**才把游标推进到「本轮最旧一篇的发布日期 − 1 天」；**没抓满**说明窗口
  已掏空，游标回 NULL，下一轮回到最新追新发文。配合跨轮去重，重复运行不会重复入库。

防重入（Phase 13）：同一任务**同一时刻只允许一轮在跑**（`_running_jobs` 闸）。调度循环本身
「先认领再串行执行」不会自我重叠；闸主要拦「立即运行」与正在跑的定时轮次并发（会导致
重复建任务、重复提取、游标双写）。轮次耗时超过间隔属正常场景：`next_run_at` 锚定在轮次
**开始**时刻，慢轮次跑完后下一个 tick 立刻补一轮——连续追赶但**不堆积**，同一时刻仍只有一轮。

时间口径：daily/weekly 的 `run_time` 按**服务器本地时间**解释；`next_run_at` 存 timestamptz。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from app.db import repositories as repo
from app.pipeline.crawl_graph import (
    _effective,
    _search_policies,
    _to_policy_row,
    run_extraction_phase,
)

logger = logging.getLogger(__name__)

_TICK_SECONDS = 15.0
_DEFAULT_RUN_TIME = time(9, 0)
# record_schedule_run 的 status 取值（与 schedule_job.last_status 对应）
_RUN_COMPLETED = "completed"
_RUN_FAILED = "failed"
_RUN_NOOP = "noop"

_WEEKDAY_LABEL = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_loop_task: asyncio.Task | None = None

# 在跑的定时任务轮次（进程内防重入闸）：同一任务同一时刻只允许一轮（见模块 docstring）。
# 单进程语义，与 crawl_graph._running 登记簿同类假设。
_running_jobs: set[int] = set()


def is_schedule_job_running(job_id: int) -> bool:
    """该定时任务当前是否有一轮在执行（供「立即运行」接口提前 409）。"""
    return job_id in _running_jobs


def describe_schedule(
    kind: str, interval_minutes: int | None, run_time: time | None, run_weekday: int | None
) -> str:
    """人读的周期描述（默认任务名 / 日志用）。"""
    if kind == "minutely":
        return f"每 {interval_minutes or 30} 分钟"
    if kind == "hourly":
        return f"每 {(interval_minutes or 60) // 60} 小时"
    if kind == "daily":
        return f"每天 {run_time or _DEFAULT_RUN_TIME:%H:%M}"
    return f"每周{_WEEKDAY_LABEL[run_weekday or 0]} {run_time or _DEFAULT_RUN_TIME:%H:%M}"


def compute_next_run(
    kind: str,
    interval_minutes: int | None,
    run_time: time | None,
    run_weekday: int | None,
    *,
    after: datetime,
) -> datetime:
    """严格晚于 `after` 的下一个触发点（本地时间，aware）。"""
    if kind == "minutely":
        return after + timedelta(minutes=interval_minutes or 30)
    if kind == "hourly":
        return after + timedelta(minutes=interval_minutes or 60)
    at = run_time or _DEFAULT_RUN_TIME
    candidate = after.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if kind == "daily":
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate
    # weekly：run_weekday 对齐 Python weekday()（0=周一 … 6=周日）
    candidate += timedelta(days=((run_weekday or 0) - candidate.weekday()) % 7)
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


def next_run_of(job: dict[str, Any], *, after: datetime) -> datetime:
    """从任务行直接算下一个触发点（调度循环 / 启停 / 手工触发共用）。"""
    return compute_next_run(
        job["schedule_kind"],
        job.get("interval_minutes"),
        job.get("run_time"),
        job.get("run_weekday"),
        after=after,
    )


async def run_schedule_job(job_id: int, *, manual: bool = False) -> bool:
    """执行一轮定时任务。任何异常都会落成 `last_status=failed` 并保留原游标（下轮重试）。

    `manual`（立即运行按钮）允许触发已停用的任务，但不改变其停用状态。
    返回是否真正执行了本轮（False = 该任务已有一轮在跑，被防重入闸拦下）。
    """
    if job_id in _running_jobs:
        logger.warning("定时任务 %s 已有一轮在执行，跳过本次触发（manual=%s）", job_id, manual)
        return False
    _running_jobs.add(job_id)
    try:
        return await _run_schedule_job_locked(job_id, manual=manual)
    finally:
        _running_jobs.discard(job_id)


async def _run_schedule_job_locked(job_id: int, *, manual: bool) -> bool:
    """`run_schedule_job` 的实际执行体（调用方已持有防重入闸）。"""
    job = await repo.fetch_schedule_job(job_id)
    if job is None:
        logger.warning("定时任务 %s 不存在，跳过", job_id)
        return True
    if not job["enabled"] and not manual:
        return True

    now = datetime.now().astimezone()
    name = job["name"]
    # 窗口：游标决定本轮是「追新」还是「向更早回填」
    cursor: date | None = job.get("cursor_before")
    date_from: date | None = job.get("date_from")
    date_to = cursor - timedelta(days=1) if cursor else date.today()

    status = _RUN_NOOP
    task_id: int | None = None
    new_cursor = cursor  # 默认不推进；异常时保持原值下轮重试
    try:
        if date_from and date_to < date_from:
            # 游标已推进到回溯起点之前：回填完毕，回到追新
            new_cursor = None
        else:
            cap = _effective(job, "pipeline_max_policies")
            # 全库去重：本任务历史轮次 ∪ 成果库（其它任务已提取成功的），跨任务不重复提取
            exclude = await repo.fetch_schedule_job_urls(job_id) | await repo.fetch_library_urls()
            items = await _search_policies(
                list(job["keywords"] or []),
                date_from,
                date_to,
                list(job["agencies"] or []),
                list(job["doc_types"] or []),
                overrides=job,
                exclude_urls=exclude,
                target_new=cap,
            )
            rows = [row for item in items if (row := _to_policy_row(item, job["source"]))]

            # 入库判别（Phase 13）：检索循环内已逐页判别并把标注写回行上（见 _search_policies），
            # 这里直接剔除「不建议入库」的篇目（批复/函等事务性文件）。判别服务不可达时
            # fail-open（检索侧就按建议入库计数、不会少抓），只告警。
            judge_dropped = sum(1 for row in rows if row.get("judge_tagged"))
            rows = [row for row in rows if not row.get("judge_tagged")][:cap]
            if judge_dropped:
                logger.info(
                    "定时任务 %s（%s）判别剔除 %d 篇不建议入库的篇目", job_id, name, judge_dropped
                )

            if not rows:
                # 无新增（或全被判别剔除）：窗口掏空，游标回 NULL，下一轮从最新追起
                new_cursor = None
            else:
                task_id = await repo.create_task(
                    source=job["source"],
                    date_from=date_from,
                    date_to=date_to,
                    keywords=list(job["keywords"] or []),
                    agencies=list(job["agencies"] or []),
                    doc_types=list(job["doc_types"] or []),
                    pipeline_overrides={
                        key: job.get(key)
                        for key in (
                            "pipeline_max_policies",
                            "pipeline_per_keyword_limit",
                            "pipeline_extract_concurrency",
                            "pipeline_max_body_chars",
                            "pipeline_mcp_timeout",
                            "pipeline_model",
                        )
                    },
                    schedule_job_id=job_id,
                )
                await repo.set_task_running(task_id, f"定时任务「{name}」触发，正在检索…")
                await repo.add_task_log(
                    task_id, f"定时任务「{name}」触发（第 {job['run_count'] + 1} 次）", tone="info"
                )
                if exclude:
                    await repo.add_task_log(
                        task_id, f"跨轮/全库去重：跳过已入库的 {len(exclude)} 个链接", tone="info"
                    )
                await repo.insert_policies(task_id, rows)
                await repo.refresh_task_counts(task_id)
                await repo.add_task_log(
                    task_id,
                    f"检索完成：获取 {len(rows)} 篇候选原文"
                    + (f"（判别剔除 {judge_dropped} 篇不建议入库）" if judge_dropped else ""),
                    tone="info",
                )

                # 游标推进：抓满上限（= cap 篇**建议入库**，rows 已剔除被判别的）→ 向更早回填一格；
                # 没抓满 → 窗口掏空，回到追新
                dates = [row["publish_date"] for row in rows if row.get("publish_date")]
                if len(rows) >= cap and dates:
                    new_cursor = min(dates) - timedelta(days=1)
                    if date_from and new_cursor < date_from:
                        new_cursor = None  # 已回填到回溯起点
                elif len(rows) < cap:
                    new_cursor = None

                # 自动确认全部篇目（跳过人工确认环节），直接跑提取段
                await repo.set_task_running(
                    task_id, f"已检索到 {len(rows)} 篇，定时任务自动确认并开始提取"
                )
                await repo.add_task_log(task_id, "定时任务自动确认全部篇目，开始提取", tone="info")
                await run_extraction_phase(task_id)
                final = await repo.fetch_task(task_id) or {}
                status = (
                    _RUN_COMPLETED if final.get("status") == "completed" else _RUN_FAILED
                )
    except Exception as exc:  # noqa: BLE001 - 一轮失败不能拖垮调度循环
        logger.exception("定时任务 %s（%s）执行失败", job_id, name)
        status = _RUN_FAILED
        new_cursor = cursor  # 游标不推进，下一轮重试同一窗口
        if task_id is not None:
            message = " ".join(str(exc).split())[:180] or exc.__class__.__name__
            await repo.add_task_log(task_id, f"定时任务执行异常：{message}", tone="info")
            try:
                await repo.finish_task(task_id, "failed", f"定时任务执行异常：{message}")
            except Exception:  # noqa: BLE001 - 落终态失败不覆盖原始异常
                logger.warning("定时任务 %s 的失败任务 %s 落终态失败", job_id, task_id)
    finally:
        next_run = next_run_of(job, after=now)
        await repo.record_schedule_run(
            job_id,
            status=status,
            next_run_at=next_run,
            cursor_before=new_cursor,
            last_task_id=task_id,
        )
        logger.info(
            "定时任务 %s（%s）本轮结束：%s，下次触发 %s，游标 %s",
            job_id, name, status, next_run, new_cursor,
        )
    return True


# ---------- 调度循环 ----------


async def _scheduler_loop() -> None:
    while True:
        try:
            now = datetime.now().astimezone()
            due = await repo.fetch_due_schedule_jobs(now)
            for job in due:
                try:
                    # 先认领（把 next_run_at 推远）再执行：执行耗时超过 tick 也不会同 tick 重复触发
                    await repo.set_schedule_job_next_run(job["id"], next_run_of(job, after=now))
                    await run_schedule_job(job["id"])
                except Exception:  # noqa: BLE001 - 单个任务失败不阻塞其他任务
                    logger.exception("定时任务 %s 触发失败", job["id"])
        except Exception:  # noqa: BLE001 - 数据库暂时不可用等，下一 tick 再试
            logger.exception("定时调度 tick 异常")
        await asyncio.sleep(_TICK_SECONDS)


async def start_scheduler() -> None:
    """启动调度循环；为缺失 next_run_at 的启用中任务补排程（重启恢复的关键）。

    由 lifespan 在连接池就绪后调用；数据库不可用时只告警（与 open_pool 一致），不阻断启动。
    """
    global _loop_task
    try:
        now = datetime.now().astimezone()
        for job in await repo.fetch_schedule_jobs(enabled_only=True):
            if job.get("next_run_at") is None:
                await repo.set_schedule_job_next_run(job["id"], next_run_of(job, after=now))
    except Exception:  # noqa: BLE001
        logger.warning("定时调度器初始化排程失败（数据库不可用？）", exc_info=True)
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_scheduler_loop(), name="schedule-loop")


async def shutdown_scheduler() -> None:
    """关停调度循环（由 lifespan 调用；在跑的轮次由 shutdown_pipelines 一并取消）。"""
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)
        _loop_task = None


def trigger_schedule_job(job_id: int) -> None:
    """「立即运行」：不排队、不等下一触发点，立刻在后台跑一轮。"""
    asyncio.create_task(run_schedule_job(job_id, manual=True), name=f"schedule-run-{job_id}")
