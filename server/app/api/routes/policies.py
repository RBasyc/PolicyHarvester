"""政策文件与提取结果路由。

- GET  /api/policies/{id}/extraction   详情抽屉三栏（事实 / 信息 / 知识）
- POST /api/policies/{id}/retry        提取失败重试：failed → pending
- POST /api/policies/{id}/reextract    重新提取：清空旧结果 → extracting

两个动作接口在落库/状态流转之后，都会调用 `start_single_extraction()` 在后台真正重跑该篇；
否则状态会一直停在 pending/extracting 没人处理。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db import repositories as repo
from app.pipeline.crawl_graph import start_single_extraction
from app.schemas.policy import (
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
