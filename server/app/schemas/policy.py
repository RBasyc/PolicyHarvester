"""政策文件与提取结果 DTO（「抓取结果」表格 + 详情抽屉三栏）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

ExtractStatus = str  # pending | extracting | extracted | failed（由数据库枚举约束）


class PolicyResultItem(BaseModel):
    """表格一行。"""

    id: int
    title: str
    publish_date: date | None = None
    source_url: str | None = None
    status: ExtractStatus
    extracted_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PolicyResultItem":
        return cls(
            id=row["id"],
            title=row["title"],
            publish_date=row["publish_date"],
            source_url=row["source_url"],
            status=row["status"],
            extracted_at=row.get("extracted_at"),
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
    term: str
    desc: str


class KnowledgeOut(BaseModel):
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
    points = [p["point"] for p in (raw.get("info_points") or []) if p.get("point")]
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
