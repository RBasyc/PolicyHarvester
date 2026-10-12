"""按表的读写函数（数据访问层）。

- 全部 SQL 参数化，不做字符串拼接；
- 返回的 `dict` 直接对应数据库列名，由 `app/schemas/` 里的 DTO 负责转成前端契约；
- 只做「取数 / 落库」，不含业务校验与 HTTP 语义（那些在 `app/api/routes/` 里）。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, TypedDict

from app.db.pool import get_pool

Row = dict[str, Any]


class PolicyRow(TypedDict, total=False):
    """落库一篇政策所需的字段（流水线 fetch 节点的产物，已归一化）。"""

    title: str
    source_url: str
    publish_date: date | None
    source_name: str | None
    agency: str | None
    doc_no: str | None
    fetched_by: str | None


# ---------- 读：抓取任务 ----------


async def fetch_current_task() -> Row | None:
    """最近一次抓取任务；库里没有任何任务时返回 None（前端展示空状态）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM crawl_task ORDER BY id DESC LIMIT 1")
            return await cur.fetchone()


async def fetch_task(task_id: int) -> Row | None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM crawl_task WHERE id = %s", (task_id,))
            return await cur.fetchone()


async def fetch_task_logs(task_id: int) -> list[Row]:
    """任务日志，按下标升序（前端按时间正序追加）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, tone, message, created_at
                FROM task_log
                WHERE task_id = %s
                ORDER BY created_at, id
                """,
                (task_id,),
            )
            return await cur.fetchall()


# ---------- 读：政策文件与提取结果 ----------


async def fetch_task_policies(task_id: int) -> list[Row]:
    """「抓取结果」表格数据：直接读视图 v_policy_results。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, task_id, title, publish_date, source_url, status, extracted_at
                FROM v_policy_results
                WHERE task_id = %s
                ORDER BY publish_date DESC NULLS LAST, id
                """,
                (task_id,),
            )
            return await cur.fetchall()


async def fetch_policy(policy_id: int) -> Row | None:
    """单篇政策 + 其提取状态（无 extraction 行时 status 视为 pending）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.*, COALESCE(e.status, 'pending') AS status
                FROM policy_document p
                LEFT JOIN extraction e ON e.policy_id = p.id
                WHERE p.id = %s
                """,
                (policy_id,),
            )
            return await cur.fetchone()


async def fetch_extraction_detail(policy_id: int) -> Row | None:
    """抽屉「事实 / 信息 / 知识」三栏所需的全部数据。

    返回 `{"policy": {...}, "extraction": {...} | None, "facts": [...]}`；
    extraction 为 None 表示该篇尚未提取（前端展示「未提取」态）。
    """
    policy = await fetch_policy(policy_id)
    if policy is None:
        return None

    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, status, error, retry_count, model,
                       info_summary, info_points,
                       knowledge_concepts, knowledge_fields, knowledge_extension,
                       extracted_at
                FROM extraction
                WHERE policy_id = %s
                """,
                (policy_id,),
            )
            extraction = await cur.fetchone()

            facts: list[Row] = []
            if extraction is not None:
                await cur.execute(
                    """
                    SELECT label, content
                    FROM fact_item
                    WHERE extraction_id = %s
                    ORDER BY position, id
                    """,
                    (extraction["id"],),
                )
                facts = await cur.fetchall()

    return {"policy": policy, "extraction": extraction, "facts": facts}


# ---------- 写：任务 ----------


async def create_task(
    *,
    source: str,
    content_type: str,
    date_from: date | None,
    date_to: date | None,
    schedule: str,
    keywords: list[str],
) -> int:
    """新建抓取任务（status=pending），返回 task_id。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO crawl_task
                    (source, content_type, date_from, date_to, schedule, keywords, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (source, content_type, date_from, date_to, schedule, keywords),
            )
            row = await cur.fetchone()
            return int(row["id"])


async def add_task_log(task_id: int, message: str, tone: str = "info") -> None:
    """追加一条任务日志（tone: info | success | active）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO task_log (task_id, tone, message) VALUES (%s, %s, %s)",
                (task_id, tone, message),
            )


# ---------- 写：单篇动作（重试 / 重新提取） ----------


async def mark_policy_retry(policy_id: int) -> int:
    """`failed → pending`，retry_count+1；返回新的 retry_count。

    调用方需先确认该篇当前状态确为 failed（见 `fetch_policy`）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE extraction
                SET status = 'pending', error = NULL, retry_count = retry_count + 1
                WHERE policy_id = %s
                RETURNING retry_count
                """,
                (policy_id,),
            )
            row = await cur.fetchone()
            return int(row["retry_count"]) if row else 0


async def reset_extraction(policy_id: int) -> int:
    """清空旧提取结果（含事实明细）并把状态置为 extracting；返回保留的 `retry_count`。

    注意 `retry_count` **不重置**——它是重试历史，重新提取时保留（响应体要如实回传）。

    UPSERT：该篇还没有 extraction 行时插入一条，已有则原地重置。
    既用于前端「重新提取」，也用于流水线开始处理某一篇之前（保证干净起点、可重复执行）。
    """
    async with get_pool().connection() as conn:
        async with conn.transaction():  # 清空 + 重置需原子完成
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO extraction
                        (policy_id, status, error, info_summary, info_points,
                         knowledge_concepts, knowledge_fields, knowledge_extension, extracted_at)
                    VALUES (%s, 'extracting', NULL, NULL, '[]'::jsonb, '[]'::jsonb, '{}', NULL, NULL)
                    ON CONFLICT (policy_id) DO UPDATE SET
                        status              = 'extracting',
                        error               = NULL,
                        info_summary        = NULL,
                        info_points         = '[]'::jsonb,
                        knowledge_concepts  = '[]'::jsonb,
                        knowledge_fields    = '{}',
                        knowledge_extension = NULL,
                        extracted_at        = NULL
                    RETURNING id, retry_count
                    """,
                    (policy_id,),
                )
                row = await cur.fetchone()

                await cur.execute(
                    "DELETE FROM fact_item WHERE extraction_id = %s",
                    (row["id"],),
                )
                return int(row["retry_count"] or 0)


# ---------- 写：流水线（Phase 4） ----------


async def insert_policies(task_id: int, policies: list[PolicyRow]) -> None:
    """把抓取到的政策批量落库；同一任务内按 `source_url` 去重。"""
    if not policies:
        return
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            for policy in policies:
                await cur.execute(
                    """
                    INSERT INTO policy_document
                        (task_id, title, publish_date, source_name, source_url,
                         agency, doc_no, fetched_by, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (task_id, source_url) DO NOTHING
                    """,
                    (
                        task_id,
                        policy["title"],
                        policy.get("publish_date"),
                        policy.get("source_name"),
                        policy["source_url"],
                        policy.get("agency"),
                        policy.get("doc_no"),
                        policy.get("fetched_by"),
                    ),
                )


async def update_policy_from_fulltext(
    policy_id: int, *, agency: str, doc_no: str, publish_date: date | None
) -> None:
    """用全文解析出的元信息回填；解析不到的字段保持原值（不写空值进去）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE policy_document
                SET agency       = COALESCE(NULLIF(%s, ''), agency),
                    doc_no       = COALESCE(NULLIF(%s, ''), doc_no),
                    publish_date = COALESCE(%s, publish_date)
                WHERE id = %s
                """,
                (agency, doc_no, publish_date, policy_id),
            )


async def set_task_running(task_id: int, stage: str) -> None:
    """任务进入运行态：`started_at` 只记第一次，重跑不清空已完成时间。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawl_task
                SET status = 'running',
                    stage = %s,
                    started_at = COALESCE(started_at, now()),
                    finished_at = NULL
                WHERE id = %s
                """,
                (stage, task_id),
            )


async def set_task_stage(task_id: int, stage: str) -> None:
    """只更新阶段文案（进度条靠计数推进，文案靠这里）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE crawl_task SET stage = %s WHERE id = %s",
                (stage, task_id),
            )


async def finish_task(task_id: int, status: str, stage: str) -> Row | None:
    """任务进入终态（completed / failed）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawl_task
                SET status = %s, stage = %s, finished_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (status, stage, task_id),
            )
            return await cur.fetchone()


async def refresh_task_counts(task_id: int) -> Row | None:
    """按**实际行数**重算任务计数并返回任务行。

    这是 Phase 4 的硬约束实现：`total_count`/`fetched_count` 取自 `policy_document`
    的行数，`extracted_count`/`failed_count` 取自 `extraction.status` 的分类计数——
    计数永远由行派生，不做自增，因此不存在漂移（进度面板与结果表格必然同源一致）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawl_task t
                SET total_count     = s.total,
                    fetched_count   = s.total,
                    extracted_count = s.extracted,
                    failed_count    = s.failed
                FROM (
                    SELECT %(task_id)s::bigint AS task_id,
                           COUNT(p.id) AS total,
                           COUNT(*) FILTER (WHERE COALESCE(e.status, 'pending') = 'extracted') AS extracted,
                           COUNT(*) FILTER (WHERE COALESCE(e.status, 'pending') = 'failed')    AS failed
                    FROM policy_document p
                    LEFT JOIN extraction e ON e.policy_id = p.id
                    WHERE p.task_id = %(task_id)s
                ) s
                WHERE t.id = s.task_id
                RETURNING t.*
                """,
                {"task_id": task_id},
            )
            return await cur.fetchone()


async def save_extraction(
    policy_id: int,
    *,
    model: str,
    facts: list[dict[str, str]],
    info_summary: str | None,
    info_points: list[str],
    knowledge_concepts: list[dict[str, str]],
    knowledge_fields: list[str],
    knowledge_extension: str | None,
) -> None:
    """写入提取结果（三栏 + 事实明细），状态置为 extracted。单事务，要么全成要么全不成。"""
    async with get_pool().connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO extraction
                        (policy_id, status, error, model, info_summary, info_points,
                         knowledge_concepts, knowledge_fields, knowledge_extension, extracted_at)
                    VALUES (%s, 'extracted', NULL, %s, %s, %s::jsonb, %s::jsonb, %s, %s, now())
                    ON CONFLICT (policy_id) DO UPDATE SET
                        status              = 'extracted',
                        error               = NULL,
                        model               = EXCLUDED.model,
                        info_summary        = EXCLUDED.info_summary,
                        info_points         = EXCLUDED.info_points,
                        knowledge_concepts  = EXCLUDED.knowledge_concepts,
                        knowledge_fields    = EXCLUDED.knowledge_fields,
                        knowledge_extension = EXCLUDED.knowledge_extension,
                        extracted_at        = EXCLUDED.extracted_at
                    RETURNING id
                    """,
                    (
                        policy_id,
                        model,
                        info_summary,
                        json.dumps(info_points, ensure_ascii=False),
                        json.dumps(knowledge_concepts, ensure_ascii=False),
                        knowledge_fields,
                        knowledge_extension,
                    ),
                )
                row = await cur.fetchone()

                await cur.execute(
                    "DELETE FROM fact_item WHERE extraction_id = %s", (row["id"],)
                )
                for position, fact in enumerate(facts, start=1):
                    await cur.execute(
                        """
                        INSERT INTO fact_item (extraction_id, label, content, position)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (row["id"], fact["label"], fact["content"], position),
                    )


async def mark_extraction_failed(policy_id: int, error: str) -> None:
    """单篇提取失败：保留错误原因供前端展示与重试。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO extraction (policy_id, status, error, extracted_at)
                VALUES (%s, 'failed', %s, now())
                ON CONFLICT (policy_id) DO UPDATE SET
                    status       = 'failed',
                    error        = EXCLUDED.error,
                    extracted_at = EXCLUDED.extracted_at
                """,
                (policy_id, error),
            )


async def fetch_logs_since(task_id: int, after_id: int) -> list[Row]:
    """增量日志（SSE 用）：只取比 `after_id` 新的行。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, tone, message, created_at
                FROM task_log
                WHERE task_id = %s AND id > %s
                ORDER BY id
                """,
                (task_id, after_id),
            )
            return await cur.fetchall()
