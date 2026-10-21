"""定时入库任务相关 DTO（Phase 11）。

`schedule_job` 表的一行 = 一份抓取配置快照 + 一组调度参数 + 一个滑动游标。
触发逻辑见 `app/scheduler.py`；周期档位与 `schedule_type` 枚举对应
（minutely / hourly 用 `interval_minutes` 存间隔，小时折算成分钟，统一口径）。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ScheduleKind = Literal["minutely", "hourly", "daily", "weekly"]
# 最近一次触发结果（noop = 本轮无新增，属正常跳过而非失败）
LastRunStatus = Literal["completed", "failed", "noop"]

_WEEKDAY_LABEL = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class ScheduleParams(BaseModel):
    """调度参数：四种档位的并集，按 `schedule_kind` 校验必填项。

    - minutely：`every` = 间隔分钟数（1-1440）
    - hourly：`every` = 间隔小时数（1-168），落库时折算成分钟
    - daily：`run_time`（"HH:MM"，服务器本地时间）
    - weekly：`run_time` + `run_weekday`（0=周一 … 6=周日，对齐 Python weekday()）
    """

    schedule_kind: ScheduleKind
    every: int | None = Field(None, ge=1, le=10080, description="minutely/hourly 的间隔数值")
    run_time: str | None = Field(None, description="daily/weekly 的运行时刻，HH:MM")
    run_weekday: int | None = Field(None, ge=0, le=6, description="weekly：0=周一 … 6=周日")

    @field_validator("run_time")
    @classmethod
    def _check_run_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("运行时刻格式应为 HH:MM") from exc
        return f"{parsed:%H:%M}"

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "ScheduleParams":
        if self.schedule_kind in ("minutely", "hourly"):
            if self.every is None:
                raise ValueError("请填写间隔数值")
            max_every = 1440 if self.schedule_kind == "minutely" else 168
            if self.every > max_every:
                raise ValueError(f"间隔超出上限（{max_every}）")
        if self.schedule_kind in ("daily", "weekly") and self.run_time is None:
            raise ValueError("请选择运行时刻")
        if self.schedule_kind == "weekly" and self.run_weekday is None:
            raise ValueError("请选择星期几")
        return self

    @property
    def interval_minutes(self) -> int | None:
        """落库口径：minutely = every 分钟；hourly = every × 60 分钟。"""
        if self.schedule_kind == "minutely":
            return self.every
        if self.schedule_kind == "hourly":
            return (self.every or 1) * 60
        return None


class ScheduleCreate(ScheduleParams):
    """「定时自动入库」弹窗提交体：抓取配置快照 + 调度参数。"""

    name: str | None = Field(None, max_length=100, description="任务名；缺省自动生成")
    source: str = Field("国务院政策文件库（gov.cn）", min_length=1, max_length=200)
    date_from: date | None = Field(None, description="回溯起点；null = 不限（从最新开始追）")
    keywords: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list, description="发布机构分类（bmfl），可多选")
    doc_types: list[str] = Field(default_factory=list, description="文种（pcodeJiguan），可多选")

    # 流水线参数覆盖（对齐 TaskCreate 的边界；null = 用服务端 .env 默认）
    pipeline_max_policies: int | None = Field(None, ge=1, le=50)
    pipeline_per_keyword_limit: int | None = Field(None, ge=1, le=50)
    pipeline_extract_concurrency: int | None = Field(None, ge=1, le=8)
    pipeline_max_body_chars: int | None = Field(None, ge=500, le=100_000)
    pipeline_mcp_timeout: int | None = Field(None, ge=1, le=600)
    pipeline_model: str | None = Field(None, max_length=100, description="提取模型（.env PIPELINE_MODELS 的 id）")

    @field_validator("source")
    @classmethod
    def _reject_browser_source(cls, value: str) -> str:
        """定时任务没有「浏览目标」字段，无头浏览器来源每个周期都无法执行——在入口拒绝。

        `schedule_job` 表只存关键词/筛选条件，重建任务时也带不上 `browse_goal`；
        放行只会得到一份每轮必然失败的定时任务。判定看来源适配器的
        `browser_driven` 标记（持久化的 browser_driven 来源 + 遗留伪来源都拦）。
        """
        from app.sources.browser_any import BROWSER_SOURCE_KEY, BROWSER_SOURCE_LABEL
        from app.sources.registry import get_by_label

        text = value.strip()
        if text in (BROWSER_SOURCE_KEY, BROWSER_SOURCE_LABEL):
            raise ValueError("定时任务暂不支持无头浏览器来源（需要在抓取时提供浏览目标）")
        resolved = get_by_label(text)
        if getattr(resolved, "browser_driven", False):
            raise ValueError("定时任务暂不支持无头浏览器来源（需要在抓取时提供浏览目标）")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("doc_types")
    @classmethod
    def _check_doc_types(cls, value: list[str]) -> list[str]:
        from app.schemas.task import DOC_TYPES  # 局部导入避免循环

        invalid = [v for v in value if v not in DOC_TYPES]
        if invalid:
            raise ValueError(f"未知文种：{'、'.join(invalid)}（可选：{'、'.join(DOC_TYPES)}）")
        return value

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            word = raw.strip()
            if not word:
                continue
            if len(word) > 50:
                raise ValueError(f"关键词过长（最多 50 字）：{word[:20]}…")
            if word not in cleaned:
                cleaned.append(word)
        return cleaned

    @field_validator("pipeline_model")
    @classmethod
    def _check_pipeline_model(cls, value: str | None) -> str | None:
        """与 TaskCreate 同一白名单：模型 id 必须在 .env 的 PIPELINE_MODELS 注册表里。"""
        if value is None:
            return None
        from app.config import get_model_registry  # 局部导入避免循环

        ids = [entry.id for entry in get_model_registry()]
        if value not in ids:
            raise ValueError(f"未知提取模型：{value}（可选：{'、'.join(ids)}）")
        return value


class ScheduleJobDTO(BaseModel):
    """定时任务全量状态：列表页与表单回显共用同一形状。"""

    id: int
    name: str
    source: str
    date_from: date | None = None
    keywords: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list)
    doc_types: list[str] = Field(default_factory=list)
    pipeline_max_policies: int | None = None
    pipeline_per_keyword_limit: int | None = None
    pipeline_extract_concurrency: int | None = None
    pipeline_max_body_chars: int | None = None
    pipeline_mcp_timeout: int | None = None
    pipeline_model: str | None = None

    schedule_kind: ScheduleKind
    interval_minutes: int | None = None
    run_time: str | None = None  # "HH:MM"
    run_weekday: int | None = None
    enabled: bool
    next_run_at: datetime | None = None
    cursor_before: date | None = None
    last_run_at: datetime | None = None
    last_task_id: int | None = None
    last_status: LastRunStatus | None = None
    run_count: int
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ScheduleJobDTO":
        run_time = row.get("run_time")
        return cls(
            **{k: row[k] for k in (
                "id", "name", "source", "date_from", "keywords", "agencies", "doc_types",
                "schedule_kind", "interval_minutes", "run_weekday",
                "enabled", "next_run_at", "cursor_before",
                "last_run_at", "last_task_id", "last_status", "run_count", "created_at",
            )},
            run_time=f"{run_time:%H:%M}" if run_time is not None else None,
            pipeline_max_policies=row.get("pipeline_max_policies"),
            pipeline_per_keyword_limit=row.get("pipeline_per_keyword_limit"),
            pipeline_extract_concurrency=row.get("pipeline_extract_concurrency"),
            pipeline_max_body_chars=row.get("pipeline_max_body_chars"),
            pipeline_mcp_timeout=row.get("pipeline_mcp_timeout"),
            pipeline_model=row.get("pipeline_model"),
        )

    @property
    def schedule_text(self) -> str:
        """人读的周期描述（列表页直接展示）。"""
        if self.schedule_kind == "minutely":
            return f"每 {self.interval_minutes} 分钟"
        if self.schedule_kind == "hourly":
            return f"每 {(self.interval_minutes or 60) // 60} 小时"
        if self.schedule_kind == "daily":
            return f"每天 {self.run_time}"
        return f"每周{_WEEKDAY_LABEL[self.run_weekday or 0]} {self.run_time}"


class ScheduleToggled(BaseModel):
    """启停 / 立即触发的响应：最新任务快照（启停用）。"""

    job: ScheduleJobDTO


class ScheduleTriggered(BaseModel):
    """立即触发：只是入队，进度要到抓取进度面板看。"""

    triggered: bool = True


class ScheduleDeleted(BaseModel):
    deleted: int
