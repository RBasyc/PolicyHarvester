"""政策文件与提取结果 DTO（「抓取结果」表格 + 详情抽屉三栏）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.task import FacetOption

ExtractStatus = str  # pending | extracting | extracted | failed（由数据库枚举约束）


class PolicyResultItem(BaseModel):
    """表格一行。"""

    id: int
    title: str
    publish_date: date | None = None
    source_url: str | None = None
    status: ExtractStatus
    extracted_at: datetime | None = None
    in_library: bool = False  # 已在成果库（其它任务提取成功的同一 URL）→ 确认面板置灰沉底
    judge_tagged: bool | None = None  # 入库判别标注：True=不建议入库（仅提示）；None=未判别
    judge_prob: float | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PolicyResultItem":
        return cls(
            id=row["id"],
            title=row["title"],
            publish_date=row["publish_date"],
            source_url=row["source_url"],
            status=row["status"],
            extracted_at=row.get("extracted_at"),
            in_library=bool(row.get("in_library")),
            judge_tagged=row.get("judge_tagged"),
            judge_prob=row.get("judge_prob"),
        )


class PolicySummary(BaseModel):
    """表格顶部四态统计（对应「已提取 6 · 提取中 1 · 未提取 2 · 失败 1」）。"""

    total: int = 0
    extracted: int = 0
    extracting: int = 0
    pending: int = 0
    failed: int = 0

    @classmethod
    def from_items(cls, items: list[PolicyResultItem]) -> "PolicySummary":
        summary = cls(total=len(items))
        for item in items:
            if item.status == "extracted":
                summary.extracted += 1
            elif item.status == "extracting":
                summary.extracting += 1
            elif item.status == "failed":
                summary.failed += 1
            else:
                summary.pending += 1
        return summary


class PolicyResultsResponse(BaseModel):
    items: list[PolicyResultItem]
    summary: PolicySummary


# ---------- 详情抽屉 ----------


class PolicyMeta(BaseModel):
    """抽屉头部元信息。"""

    id: int
    source_name: str | None = None
    title: str
    agency: str | None = None
    doc_no: str | None = None
    publish_date: date | None = None
    source_url: str | None = None
    fetched_by: str | None = None


class FactOut(BaseModel):
    label: str
    content: str


class InfoOut(BaseModel):
    summary: str | None = None
    points: list[str] = Field(default_factory=list)


class ConceptOut(BaseModel):
    """一条可沉淀的知识点。`term` 是知识点标题、`desc` 是其依据与做法（沿用库里的键名）。"""

    term: str
    desc: str


class KnowledgeOut(BaseModel):
    """知识区：可沉淀的知识点 + 适用领域 + 行动建议。"""

    concepts: list[ConceptOut] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    extension: str | None = None


class ExtractionOut(BaseModel):
    """三栏内容。未提取时 status=pending 且三区为空。"""

    status: ExtractStatus
    error: str | None = None
    retry_count: int = 0
    model: str | None = None
    extracted_at: datetime | None = None

    facts: list[FactOut] = Field(default_factory=list)
    info: InfoOut = Field(default_factory=InfoOut)
    knowledge: KnowledgeOut = Field(default_factory=KnowledgeOut)

    @property
    def is_empty(self) -> bool:
        """已提取但三区皆空（前端展示「暂无提取内容」）。"""
        return (
            not self.facts
            and not self.info.summary
            and not self.info.points
            and not self.knowledge.concepts
            and not self.knowledge.fields
            and not self.knowledge.extension
        )


class PolicyExtractionResponse(BaseModel):
    policy: PolicyMeta
    extraction: ExtractionOut


def _info_points(raw: Any) -> list[str]:
    """把 JSONB `info_points` 读成字符串列表。

    契约为 `[{"point": "..."}]`（见 database/schema.sql），但流水线早期版本误写成过裸字符串
    `["..."]` —— 两种都要能读，否则历史行会让抽屉接口直接 500。
    """
    points: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("point") or "").strip()
        else:
            continue
        if text:
            points.append(text)
    return points


def build_extraction_response(detail: dict[str, Any]) -> PolicyExtractionResponse:
    """把 repositories 返回的裸行转成契约形状（含「无 extraction 行」的兜底）。"""
    row = detail["policy"]
    policy = PolicyMeta(
        id=row["id"],
        source_name=row.get("source_name"),
        title=row["title"],
        agency=row.get("agency"),
        doc_no=row.get("doc_no"),
        publish_date=row.get("publish_date"),
        source_url=row.get("source_url"),
        fetched_by=row.get("fetched_by"),
    )

    raw = detail["extraction"]
    if raw is None:
        # 尚未提取：状态取自视图口径（policy 行的 COALESCE 结果）
        return PolicyExtractionResponse(
            policy=policy,
            extraction=ExtractionOut(status=row.get("status") or "pending"),
        )

    # info_points / knowledge_concepts 是 JSONB，形如 [{"point": "..."}] / [{"term":..,"desc":..}]
    points = _info_points(raw.get("info_points"))
    concepts = [
        ConceptOut(term=c["term"], desc=c.get("desc", ""))
        for c in (raw.get("knowledge_concepts") or [])
        if c.get("term")
    ]

    return PolicyExtractionResponse(
        policy=policy,
        extraction=ExtractionOut(
            status=raw["status"],
            error=raw.get("error"),
            retry_count=raw.get("retry_count") or 0,
            model=raw.get("model"),
            extracted_at=raw.get("extracted_at"),
            facts=[FactOut(**f) for f in detail["facts"]],
            info=InfoOut(summary=raw.get("info_summary"), points=points),
            knowledge=KnowledgeOut(
                concepts=concepts,
                fields=list(raw.get("knowledge_fields") or []),
                extension=raw.get("knowledge_extension"),
            ),
        ),
    )


class PolicyActionResponse(BaseModel):
    """retry / reextract 的统一返回。"""

    id: int
    status: ExtractStatus
    retry_count: int = 0


# ---------- 成果库（跨任务沉淀，仅提取成功的篇目） ----------


class LibraryPolicyItem(BaseModel):
    """成果库卡片：一行 = 一篇已提取政策（按 source_url 去重后）。"""

    id: int
    title: str
    agency: str | None = None
    doc_type: str | None = None  # 由文号前缀推导（首个括号符之前，如 国发/粤府），推导不出为 null
    publish_date: date | None = None
    summary: str | None = None  # 提取的摘要（信息栏）
    fact_count: int = 0
    point_count: int = 0
    source_url: str | None = None
    # 抓取来源（任务的 source label）。成果库跨来源混排，卡片上靠它区分是哪家站的
    # ——非 gov.cn 来源没有「文种」这类 gov.cn 专属标志，少了它就只剩标题和摘要。
    source_name: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "LibraryPolicyItem":
        return cls(
            id=row["id"],
            title=row["title"],
            agency=row.get("agency"),
            doc_type=row.get("doc_type"),
            publish_date=row.get("publish_date"),
            summary=row.get("info_summary"),
            fact_count=int(row.get("fact_count") or 0),
            point_count=int(row.get("point_count") or 0),
            source_url=row.get("source_url"),
            source_name=row.get("source_name"),
        )


class LibraryListResponse(BaseModel):
    items: list[LibraryPolicyItem]
    total: int
    page: int
    page_size: int


class LibraryMeta(BaseModel):
    """库头部统计 + 筛选下拉选项（机构 / 文种 / 年份）。"""

    total: int
    agency_count: int  # 覆盖机构（distinct 发文机关数）
    month_new: int  # 本月新增（extracted_at 落在本月）
    agencies: list[FacetOption]
    doc_types: list[FacetOption]
    years: list[FacetOption]  # name 为年份字符串


class FulltextMeta(BaseModel):
    """从已归档全文里解析出的 gov.cn 元信息表（详情页 head-card 补缺用）。

    全部 optional：字段只在原文里出现时才有值。
    """

    agency: str | None = None  # 发文机关
    doc_no: str | None = None  # 发文字号
    source: str | None = None  # 来源
    category: str | None = None  # 主题分类
    doc_kind: str | None = None  # 公文种类
    signed_date: str | None = None  # 成文日期（原文形态，如「2026年09月25日」）


class LibraryPolicyDetail(BaseModel):
    """成果库详情页：元信息 + 三栏提取结果 + 政策正文存档（旧数据可能为 null）。"""

    policy: PolicyMeta
    extraction: ExtractionOut
    fulltext: str | None = None
    fulltext_meta: FulltextMeta | None = None  # 从归档全文解析的 gov.cn 元信息表
