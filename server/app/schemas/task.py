"""抓取任务相关 DTO（对齐 PLAN.md 第三节 API 契约）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TaskStatus = Literal["pending", "running", "completed", "failed"]
ContentType = Literal["policy", "notice", "all"]
ScheduleType = Literal["manual", "daily", "weekly"]
LogTone = Literal["info", "success", "active"]

# `started_at` 距今超过这个秒数就不再外推剩余时间（老任务/演示数据上外推没有意义）
_MAX_ELAPSED_FOR_ETA = 6 * 3600


class TaskDTO(BaseModel):
    """任务全量状态：轮询与 SSE 共用同一形状。"""

    id: int
    source: str
    content_type: ContentType
    date_from: date | None = None
    date_to: date | None = None
    schedule: ScheduleType
    keywords: list[str] = Field(default_factory=list)

    status: TaskStatus
    stage: str | None = None

    # 进度与预估
    progress: int = Field(0, ge=0, le=100, description="百分比")
    eta_seconds: int | None = Field(None, description="预计剩余秒数；无估算依据时为 null")

    # 统计（前端「已抓取 10/10 · 已提取 6/10 · 失败 0」）
    total_count: int
    fetched_count: int
    extracted_count: int
    failed_count: int

    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TaskDTO":
        return cls(
            **{k: row[k] for k in (
                "id", "source", "content_type", "date_from", "date_to", "schedule",
                "keywords", "status", "stage",
                "total_count", "fetched_count", "extracted_count", "failed_count",
                "started_at", "finished_at", "created_at",
            )},
            progress=_progress(row),
            eta_seconds=_eta_seconds(row),
        )


def _progress(row: dict[str, Any]) -> int:
    """进度条：抓取阶段 0→50（`fetched/total`），提取阶段 50→100（已完成篇数占比）。

    提取阶段的分子用「已提取 + 失败」——失败篇目也算处理完，否则进度条会永远差一截。
    """
    status = row["status"]
    if status == "pending":
        return 0
    if status == "completed":
        return 100

    total = row["total_count"] or 0
    if total <= 0:
        return 0

    fetched = row["fetched_count"] or 0
    done = (row["extracted_count"] or 0) + (row["failed_count"] or 0)
    fetched = min(fetched, total)
    done = min(done, total)
    # 未完成时不显示 100%，避免「进度条满但仍在跑」的错觉
    return min(99, round(50 * fetched / total + 50 * done / total))


def _eta_seconds(row: dict[str, Any]) -> int | None:
    """用已完成篇目的平均耗时外推剩余时间；没有可靠依据时返回 None（前端不显示）。"""
    if row["status"] != "running":
        return None

    total = row["total_count"] or 0
    done = (row["extracted_count"] or 0) + (row["failed_count"] or 0)
    started_at: datetime | None = row["started_at"]
    if total <= 0 or done <= 0 or done >= total or started_at is None:
        return None

    if started_at.tzinfo is None:  # 兜底：理论上是 timestamptz，不会是 naive
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed <= 0 or elapsed > _MAX_ELAPSED_FOR_ETA:
        return None

    return int(elapsed / done * (total - done))


class TaskLogDTO(BaseModel):
    """日志流单行（time 已是前端直接展示的 `[HH:MM:SS]` 形式）。"""

    time: str
    tone: LogTone
    message: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TaskLogDTO":
        created: datetime = row["created_at"]
        return cls(time=f"[{created:%H:%M:%S}]", tone=row["tone"], message=row["message"])


class TaskCreate(BaseModel):
    """「抓取配置」表单提交体。"""

    source: str = Field("国务院政策文件库（gov.cn）", min_length=1, max_length=200)
    content_type: ContentType = "policy"
    date_from: date | None = None
    date_to: date | None = None
    schedule: ScheduleType = "manual"
    keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        """去空白、去空串、保序去重；单个关键词最长 50 字。"""
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

    @model_validator(mode="after")
    def _check_date_range(self) -> "TaskCreate":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class TaskCreated(BaseModel):
    task_id: int


class TaskDeleted(BaseModel):
    """删除 / 清空任务的响应体：**实际**删除的任务数（清空时可能为 0）。"""

    deleted: int
