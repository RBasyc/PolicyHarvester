"""政策文件与提取结果路由。

- GET  /api/policies/meta             成果库头部统计 + 筛选选项（机构/文种/年份）
- GET  /api/policies                  成果库列表（agency/doc_type/year/keyword 过滤 + 分页）
- GET  /api/policies/{id}             成果库详情页（元信息 + 三栏 + 政策正文存档）
- GET  /api/policies/{id}/extraction   详情抽屉三栏（事实 / 信息 / 知识）
- POST /api/policies/{id}/retry        提取失败重试：failed → pending
- POST /api/policies/{id}/reextract    重新提取：清空旧结果 → extracting

注册顺序注意：静态路径 `/meta` 与列表路由必须放在 `/{policy_id}` 之前，
否则 "meta" 会被路径参数吞掉（同 tasks.py 里 `/current` 前置的坑）。

两个动作接口在落库/状态流转之后，都会调用 `start_single_extraction()` 在后台真正重跑该篇；
否则状态会一直停在 pending/extracting 没人处理。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import repositories as repo
from app.pipeline.crawl_graph import start_single_extraction
from app.pipeline.fulltext_clean import split_fulltext
from app.schemas.policy import (
    LibraryListResponse,
    LibraryMeta,
    LibraryPolicyDetail,
    LibraryPolicyItem,
    PolicyActionResponse,
    PolicyExtractionResponse,
    build_extraction_response,
)

router = APIRouter(prefix="/api/policies", tags=["policies"])


async def _get_policy_or_404(policy_id: int) -> dict:
    row = await repo.fetch_policy(policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"政策文件不存在：{policy_id}")
    return row


# ---------- 成果库 ----------


@router.get("/meta", response_model=LibraryMeta)
async def get_library_meta() -> LibraryMeta:
    """库头部三项统计（总篇数/覆盖机构/本月新增）+ 筛选下拉选项。"""
    return LibraryMeta(**await repo.fetch_library_meta())


@router.get("", response_model=LibraryListResponse)
async def list_library_policies(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    agency: str | None = Query(None, max_length=100),
    doc_type: str | None = Query(None, max_length=20),
    year: int | None = Query(None, ge=1900, le=2100),
) -> LibraryListResponse:
    """成果库列表：仅提取成功的篇目，按 source_url 去重（重新执行任务不产生重复条目）。

    固定按发布日期倒序；文种由文号前缀推导。keyword 过滤是包含匹配（ILIKE）。
    """
    kw = keyword.strip() if keyword else None
    filters = {
        "agency": agency.strip() if agency else None,
        "doc_type": doc_type.strip() if doc_type else None,
        "year": year,
        "keyword": kw,
    }
    total = await repo.count_library_policies(**filters)
    items = await repo.fetch_library_policies(
        **filters, limit=page_size, offset=(page - 1) * page_size
    )
    return LibraryListResponse(
        items=[LibraryPolicyItem.from_row(row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{policy_id}", response_model=LibraryPolicyDetail)
async def get_library_policy(policy_id: int) -> LibraryPolicyDetail:
    """成果库详情页：政策元信息 + 三栏提取结果 + 政策正文存档（旧数据无正文时为 null）。

    历史归档的正文可能是「整页文本」时代的脏数据（混有 CSS/JS/导航与元信息表），
    读取时统一过一遍 `split_fulltext`：元信息提出来给 head-card 补缺，正文只留原文。
    """
    detail = await repo.fetch_extraction_detail(policy_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"政策文件不存在：{policy_id}")
    base = build_extraction_response(detail)
    archived = detail["policy"].get("fulltext")
    fulltext_meta = fulltext_body = None
    if archived:
        fulltext_meta, fulltext_body = split_fulltext(archived)
    return LibraryPolicyDetail(
        policy=base.policy,
        extraction=base.extraction,
        fulltext=fulltext_body,
        fulltext_meta=fulltext_meta,
    )


@router.get("/{policy_id}/extraction", response_model=PolicyExtractionResponse)
async def get_policy_extraction(policy_id: int) -> PolicyExtractionResponse:
    """抽屉三栏数据；未提取的篇目返回 status=pending 且三区为空。"""
    detail = await repo.fetch_extraction_detail(policy_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"政策文件不存在：{policy_id}")
    return build_extraction_response(detail)


@router.post("/{policy_id}/retry", response_model=PolicyActionResponse)
async def retry_policy(policy_id: int) -> PolicyActionResponse:
    """失败重试：仅 `failed` 状态可重试，`retry_count` 自增并记日志。"""
    policy = await _get_policy_or_404(policy_id)
    if policy["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态为 {policy['status']}，只有提取失败的政策可以重试",
        )

    retry_count = await repo.mark_policy_retry(policy_id)
    await repo.add_task_log(
        policy["task_id"],
        f"《{policy['title']}》已重新排队提取（第 {retry_count} 次重试）",
        tone="info",
    )
    # 排队后立刻在后台真正重跑这一篇（否则状态会一直停在 pending）
    start_single_extraction(policy["task_id"], policy_id)
    return PolicyActionResponse(id=policy_id, status="pending", retry_count=retry_count)


@router.post("/{policy_id}/reextract", response_model=PolicyActionResponse)
async def reextract_policy(policy_id: int) -> PolicyActionResponse:
    """重新提取：清空旧提取结果与事实明细，状态置为 `extracting`。"""
    policy = await _get_policy_or_404(policy_id)
    if policy["status"] == "extracting":
        raise HTTPException(status_code=409, detail="该篇正在提取中，请稍候")

    retry_count = await repo.reset_extraction(policy_id)
    await repo.add_task_log(
        policy["task_id"],
        f"《{policy['title']}》已重新提交提取",
        tone="active",
    )
    start_single_extraction(policy["task_id"], policy_id)
    return PolicyActionResponse(id=policy_id, status="extracting", retry_count=retry_count)
