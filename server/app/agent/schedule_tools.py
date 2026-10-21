"""定时入库任务的对话入口（Agent 工具 `manage_schedule`）。

**为什么需要**：调度器（`app/scheduler.py`）与页面（`/api/schedules`）早就有了
「每天/每小时自动抓一次并入库」的能力，**只有对话侧够不着**——用户说
「每天盯着浙江科技政策」时，Agent 只能建议他去页面上建。

实现与页面**同一段代码**（`app/services/schedule.py`），一处实现两个出入口。

**要说清的一件事**：调度器触发一轮后会**自动确认并入库**，**绕过**对话侧那道人工
确认闸（既有设计）。所以这个工具在用户没明确说清「自动」之前不要用——
用户授权的是「每天自动抓」，不是「每天自动花提取的钱」。

**工具不抛异常**：任何失败都返回 `{"ok": false, "error": "<中文原因>"}`。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import ValidationError

from app.agent.tool_utils import dump as _dump
from app.agent.tool_utils import error as _error
from app.agent.tool_utils import parse_iso_date as _parse_date
from app.agent.tool_utils import validation_message as _validation_message

_ACTIONS = ("list", "create", "toggle", "run", "delete")

_AUTO_CONFIRM_NOTE = (
    "定时任务**会自动确认并入库**（调度器绕过对话侧的人工确认闸），每轮都会花提取的钱——"
    "用户没明确说要「自动」之前，先问一句。"
)


@tool
async def manage_schedule(
    action: str,
    job_id: int | None = None,
    name: str | None = None,
    source: str | None = None,
    keywords: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    schedule_kind: str = "daily",
    every: int | None = None,
    run_time: str | None = None,
    run_weekday: int | None = None,
    max_policies: int | None = None,
) -> str:
    """管理定时入库任务：按周期自动抓取某个来源的关键词并入库。

    五个动作：

    - `list`    列出定时任务（名称 / 周期 / 来源 / 关键词 / 是否启用 / 下次触发时间）。
    - `create`  新建一个定时任务（抓取配置快照 + 周期）。
    - `toggle`  启用 / 停用（停用即不排程，配置保留）。
    - `run`     立即触发一轮（不等下一个触发点；后台执行）。
    - `delete`  删除定时任务（已触发过的抓取任务保留，可追溯）。

    **周期档位**：`schedule_kind` 取 `minutely` / `hourly` / `daily` / `weekly`；
    `minutely`/`hourly` 要给 `every`（间隔数，小时档位单位是小时）；
    `daily` 给 `run_time`（"HH:MM"）；`weekly` 还要给 `run_weekday`（0=周一 … 6=周日）。

    **要当心的一件事**：定时任务触发后会**自动确认并入库**（调度器绕过对话侧的人工
    确认闸），每轮都会花提取的钱。用户没明确说要「自动」之前，先跟他确认。

    Args:
        action: `list` / `create` / `toggle` / `run` / `delete` 之一。
        job_id: 定时任务 id（`list` 的返回里有）；`toggle` / `run` / `delete` 用。
        name: 任务名（`create`）。
        source: 抓取来源的展示名或 key（`create`；留空 = 国务院政策文件库）。
        keywords: 检索关键词（`create`）。
        date_from / date_to: 时间范围（YYYY-MM-DD，`create`）。
        agencies / doc_types: 发布机构分类与文种（`create`，gov.cn 专有）。
        schedule_kind: 周期档位（`create`）。
        every: 间隔数（`create`，minutely=分钟 / hourly=小时）。
        run_time: 运行时刻 "HH:MM"（`create`，daily / weekly）。
        run_weekday: 星期几 0=周一…6=周日（`create`，weekly）。
        max_policies: 每轮最多处理几篇（`create`，1–50；**提取按篇计费**）。

    Returns:
        JSON 文本。`ok=true` 时含任务快照（`list` 时是数组）与 `next`；
        失败时 `ok=false` + 中文原因。
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"action 需要是 {' / '.join(_ACTIONS)} 之一，收到：{action or '（空）'}")

    try:
        from app.services import schedule as svc
    except Exception as exc:  # noqa: BLE001 - 依赖装不全时也要给模型一句人话
        return _error(f"定时任务服务不可用：{exc}")

    try:
        if action == "list":
            jobs = await svc.list_jobs()
            return _dump(
                {
                    "ok": True,
                    "action": "list",
                    "count": len(jobs),
                    "jobs": jobs,
                    "note": _AUTO_CONFIRM_NOTE,
                }
            )

        if action in ("toggle", "run", "delete"):
            if job_id is None:
                return _error(f"{action} 需要 job_id（list 的返回里有）")
            if action == "toggle":
                row = await svc.toggle_job(job_id)
                return _dump({"ok": True, "action": action, "job": row, "note": _AUTO_CONFIRM_NOTE})
            if action == "run":
                result = await svc.run_job(job_id)
                return _dump(
                    {
                        "ok": True,
                        "action": action,
                        **result,
                        "next": "已入队触发一轮：这一轮会自动确认并入库，进度到「抓取进度」面板看。",
                    }
                )
            result = await svc.delete_job(job_id)
            return _dump({"ok": True, "action": action, **result})

        # create
        payload, err = _build_payload(
            name=name,
            source=source,
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            agencies=agencies,
            doc_types=doc_types,
            schedule_kind=schedule_kind,
            every=every,
            run_time=run_time,
            run_weekday=run_weekday,
            max_policies=max_policies,
        )
        if err:
            return _error(err)
        row = await svc.create_job(payload)
        return _dump(
            {
                "ok": True,
                "action": "create",
                "job": row,
                "note": _AUTO_CONFIRM_NOTE,
                "next": "定时任务已建好（页面「定时任务」里也能看到）。每轮会自动确认并入库。",
            }
        )
    except svc.ScheduleNotFound as exc:
        return _error(str(exc))
    except svc.ScheduleConflict as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底：工具绝不抛异常
        return _error(f"定时任务操作失败：{exc}")


def _build_payload(**kwargs: Any) -> tuple[Any, str | None]:
    """拼 `ScheduleCreate`；返回 `(payload, error)`——两者不会同时有值。

    校验交给 schema（`ScheduleCreate` 的字段校验器：文种白名单、无头浏览器来源、
    提取模型白名单、周期必填项……），**不要在这里另写一套**。
    """
    from app.schemas.schedule import ScheduleCreate

    start, err = _parse_date(kwargs.get("date_from"), "date_from")
    if err:
        return None, err
    end, err = _parse_date(kwargs.get("date_to"), "date_to")
    if err:
        return None, err

    fields: dict[str, Any] = {
        "name": kwargs.get("name"),
        "source": (kwargs.get("source") or "国务院政策文件库（gov.cn）").strip(),
        "keywords": list(kwargs.get("keywords") or []),
        "agencies": list(kwargs.get("agencies") or []),
        "doc_types": list(kwargs.get("doc_types") or []),
        "schedule_kind": kwargs.get("schedule_kind") or "daily",
        "every": kwargs.get("every"),
        "run_time": kwargs.get("run_time"),
        "run_weekday": kwargs.get("run_weekday"),
    }
    if start:
        fields["date_from"] = start
    if end:
        fields["date_to"] = end
    max_policies = kwargs.get("max_policies")
    if max_policies:
        fields["pipeline_max_policies"] = max_policies

    try:
        return ScheduleCreate(**fields), None
    except ValidationError as exc:
        return None, _validation_message(exc)


# 注册表：`app/agent/tools.py` 把它并进 TOOLS
SCHEDULE_TOOLS = [manage_schedule]
