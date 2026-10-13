"""抓取任务相关 DTO（对齐 PLAN.md 第三节 API 契约）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TaskStatus = Literal["pending", "running", "awaiting_confirmation", "completed", "failed"]
ContentType = Literal["policy", "notice", "all"]  # 已弃用：对抓取无过滤作用，仅遗留列保留
ScheduleType = Literal["manual", "daily", "weekly"]  # 已弃用：demo 不执行定时，仅遗留列保留
LogTone = Literal["info", "success", "active"]

# 文种（gov.cn 检索 pcodeJiguan 参数的可选值）
DOC_TYPES: tuple[str, ...] = ("国令", "国发", "国函", "国办发", "国办函")

# `started_at` 距今超过这个秒数就不再外推剩余时间（老任务/演示数据上外推没有意义）
_MAX_ELAPSED_FOR_ETA = 6 * 3600


class TaskDTO(BaseModel):
    """任务全量状态：轮询与 SSE 共用同一形状。"""

    id: int
    source: str
    date_from: date | None = None
    date_to: date | None = None
    keywords: list[str] = Field(default_factory=list)
    # gov.cn 检索筛选项（发布机构分类 bmfl / 文种 pcodeJiguan；对齐官网筛选面板）
    agencies: list[str] = Field(default_factory=list)
    doc_types: list[str] = Field(default_factory=list)

    # 本任务覆盖的流水线参数（页面「高级设置」；None = 用服务端 .env 默认）
    pipeline_max_policies: int | None = Field(None, ge=1, le=50)
    pipeline_per_keyword_limit: int | None = Field(None, ge=1, le=50)
    pipeline_extract_concurrency: int | None = Field(None, ge=1, le=8)
    pipeline_max_body_chars: int | None = Field(None, ge=500, le=100_000)
    pipeline_mcp_timeout: int | None = Field(None, ge=1, le=600)
    pipeline_model: str | None = Field(None, description="提取模型（PIPELINE_MODELS 注册表 id；None = 默认模型）")

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
        # 新列用 .get(...) 防御未迁移的老库（硬下标 row[k] 会在 KeyError 时 500）
        return cls(
            **{k: row[k] for k in (
                "id", "source", "date_from", "date_to",
                "keywords", "status", "stage",
                "total_count", "fetched_count", "extracted_count", "failed_count",
                "started_at", "finished_at", "created_at",
            )},
            agencies=list(row.get("agencies") or []),
            doc_types=list(row.get("doc_types") or []),
            # 本任务的流水线参数覆盖（老库没有这些列时为 None = 用全局默认）
            pipeline_max_policies=row.get("pipeline_max_policies"),
            pipeline_per_keyword_limit=row.get("pipeline_per_keyword_limit"),
            pipeline_extract_concurrency=row.get("pipeline_extract_concurrency"),
            pipeline_max_body_chars=row.get("pipeline_max_body_chars"),
            pipeline_mcp_timeout=row.get("pipeline_mcp_timeout"),
            pipeline_model=row.get("pipeline_model"),
            progress=_progress(row),
            eta_seconds=_eta_seconds(row),
        )


def _progress(row: dict[str, Any]) -> int:
    """进度条：抓取阶段 0→50（`fetched/total`），提取阶段 50→100（已完成篇数占比）。

    提取阶段的分子用「已提取 + 失败」——失败篇目也算处理完，否则进度条会永远差一截。
    `awaiting_confirmation`（抓完了、还没确认）自然落在 50%：`fetched == total` 且一篇都没提取。
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
    date_from: date | None = None
    date_to: date | None = None
    keywords: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list, description="发布机构分类（bmfl），可多选")
    doc_types: list[str] = Field(default_factory=list, description="文种（pcodeJiguan），可多选")

    # 本任务覆盖的流水线参数（页面「高级设置」；不传 / 传 null = 用服务端 .env 默认）
    pipeline_max_policies: int | None = Field(None, ge=1, le=50, description="单次任务最多处理多少篇")
    pipeline_per_keyword_limit: int | None = Field(None, ge=1, le=50, description="每个关键词向 MCP 请求多少条")
    pipeline_extract_concurrency: int | None = Field(None, ge=1, le=8, description="并发提取篇数")
    pipeline_max_body_chars: int | None = Field(None, ge=500, le=100_000, description="送进大模型的正文上限")
    pipeline_mcp_timeout: int | None = Field(None, ge=1, le=600, description="单次 MCP 调用超时秒数")
    pipeline_model: str | None = Field(None, max_length=100, description="提取模型（.env PIPELINE_MODELS 的 id）")

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

    @field_validator("agencies")
    @classmethod
    def _clean_agencies(cls, value: list[str]) -> list[str]:
        return _dedup_clean(value, "发布机构")

    @field_validator("doc_types")
    @classmethod
    def _clean_doc_types(cls, value: list[str]) -> list[str]:
        cleaned = _dedup_clean(value, "文种")
        invalid = [v for v in cleaned if v not in DOC_TYPES]
        if invalid:
            raise ValueError(f"未知文种：{'、'.join(invalid)}（可选：{'、'.join(DOC_TYPES)}）")
        return cleaned

    @field_validator("pipeline_model")
    @classmethod
    def _check_pipeline_model(cls, value: str | None) -> str | None:
        """模型 id 必须在 .env 的 PIPELINE_MODELS 注册表里（与 doc_types 白名单同一模式）。"""
        if value is None:
            return None
        from app.config import get_model_registry  # 局部导入避免循环

        ids = [entry.id for entry in get_model_registry()]
        if value not in ids:
            raise ValueError(f"未知提取模型：{value}（可选：{'、'.join(ids)}）")
        return value

    @model_validator(mode="after")
    def _check_date_range(self) -> "TaskCreate":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("开始日期不能晚于结束日期")
        if self.date_from is None and self.date_to is not None:
            raise ValueError("设置结束日期时也要设置开始日期")
        return self


def _dedup_clean(value: list[str], label: str, max_len: int = 100) -> list[str]:
    """筛选项共用清洗：去空白、去空串、保序去重、限长。"""
    cleaned: list[str] = []
    for raw in value:
        word = raw.strip()
        if not word:
            continue
        if len(word) > max_len:
            raise ValueError(f"{label}过长（最多 {max_len} 字）：{word[:20]}…")
        if word not in cleaned:
            cleaned.append(word)
    return cleaned


class FacetOption(BaseModel):
    """单个筛选项（名字 + 当前检索条件下的条数）。"""

    name: str
    count: int


class FacetList(BaseModel):
    """筛选器可选值：优先实时 facet（带条数），失败时回退静态名单。"""

    agencies: list[FacetOption]
    doc_types: list[FacetOption]
    source: Literal["live", "fallback"]


class TaskCreated(BaseModel):
    task_id: int


class TaskConfirm(BaseModel):
    """「检索结果确认」提交体：用户勾选要抓取的篇目（其余在确认时被丢弃）。"""

    policy_ids: list[int] = Field(default_factory=list, description="勾选的政策 id，至少一个")


class TaskDeleted(BaseModel):
    """删除 / 清空任务的响应体：**实际**删除的任务数（清空时可能为 0）。"""

    deleted: int
