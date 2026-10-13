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
    catalog: str | None
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
                SELECT id, task_id, title, publish_date, source_url, catalog, status, extracted_at
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
    date_from: date | None,
    date_to: date | None,
    keywords: list[str],
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    pipeline_overrides: dict[str, int | None] | None = None,
) -> int:
    """新建抓取任务（status=pending），返回 task_id。

    `pipeline_overrides` 是页面「高级设置」提交的本任务参数（键为 `pipeline_*` 列名）；
    覆盖值必须**落库**而不是只留在请求里：第二段提取是确认后从库里重建的，
    到那时请求早已结束，只有任务行上的值还能读到。NULL = 用服务端 .env 默认。

    已弃用的 content_type / schedule 列交给 schema 默认值兜底，不再写入。
    """
    overrides = pipeline_overrides or {}
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO crawl_task
                    (source, date_from, date_to, keywords, agencies, doc_types, status,
                     pipeline_max_policies, pipeline_per_keyword_limit,
                     pipeline_extract_concurrency, pipeline_max_body_chars, pipeline_mcp_timeout)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    source, date_from, date_to, keywords, agencies or [], doc_types or [],
                    overrides.get("pipeline_max_policies"),
                    overrides.get("pipeline_per_keyword_limit"),
                    overrides.get("pipeline_extract_concurrency"),
                    overrides.get("pipeline_max_body_chars"),
                    overrides.get("pipeline_mcp_timeout"),
                ),
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
                         agency, doc_no, catalog, fetched_by, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
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
                        policy.get("catalog"),
                        policy.get("fetched_by"),
                    ),
                )


async def update_policy_from_fulltext(
    policy_id: int, *, agency: str, doc_no: str, publish_date: date | None, fulltext: str | None = None
) -> None:
    """用全文解析出的元信息回填；解析不到的字段保持原值（不写空值进去）。

    同时把全文正文存档到 `fulltext` 列（成果库详情页「政策正文」用）；
    正文只在非空时覆盖，重试时拿到空正文不会清掉旧存档。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE policy_document
                SET agency       = COALESCE(NULLIF(%s, ''), agency),
                    doc_no       = COALESCE(NULLIF(%s, ''), doc_no),
                    publish_date = COALESCE(%s, publish_date),
                    fulltext     = COALESCE(NULLIF(%s, ''), fulltext)
                WHERE id = %s
                """,
                (agency, doc_no, publish_date, fulltext or "", policy_id),
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


async def await_task_confirmation(task_id: int, stage: str) -> None:
    """任务停在「检索完成、等用户确认要抓哪些」这一档（非终态，SSE 不会结束）。

    与 `set_task_running` 的区别：不改 `started_at`（抓取阶段已经开始），也不写 `finished_at`。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE crawl_task
                SET status = 'awaiting_confirmation', stage = %s
                WHERE id = %s
                """,
                (stage, task_id),
            )


async def discard_unselected_policies(task_id: int, keep_ids: list[int]) -> int:
    """确认抓取时丢弃未勾选的篇目（直接删行），返回删除条数。

    调用方需保证 `keep_ids` 非空——空列表会被解释成 `NOT (id = ANY('{}'))`，即删光该任务全部篇目。
    删除后调用方必须紧跟一次 `refresh_task_counts()`。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM policy_document
                WHERE task_id = %s AND NOT (id = ANY(%s))
                """,
                (task_id, keep_ids),
            )
            return cur.rowcount


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


async def fail_stale_tasks(reason: str) -> list[int]:
    """把上次进程遗留的 `pending`/`running` 任务落为 `failed` 终态，返回受影响的 task_id。

    重启后这些任务的 asyncio 流水线已随上个进程消失，若不打标，前端会永远显示「运行中」
    且无法重新开始（`GET /tasks/current` 返回的就是这条僵尸任务）。启动时调用一次。
    """
    async with get_pool().connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE crawl_task
                    SET status = 'failed', stage = %s, finished_at = now()
                    WHERE status IN ('pending', 'running')
                    RETURNING id
                    """,
                    (reason,),
                )
                ids = [int(row["id"]) for row in await cur.fetchall()]
                for task_id in ids:
                    await cur.execute(
                        """
                        INSERT INTO task_log (task_id, tone, message)
                        VALUES (%s, 'info', %s)
                        """,
                        (task_id, reason),
                    )
                return ids


# ---------- 写：删除与清空 ----------


async def delete_task(task_id: int) -> bool:
    """删除一个抓取任务；政策文件 / 提取结果 / 事实 / 日志由外键 `ON DELETE CASCADE` 一并清除。

    返回是否真的删到了行（`False` 表示该任务本就不存在）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM crawl_task WHERE id = %s RETURNING id", (task_id,)
            )
            return await cur.fetchone() is not None


async def delete_all_tasks() -> int:
    """清空全部抓取任务（级联删除其结果），返回删除的任务数。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM crawl_task RETURNING id")
            return len(await cur.fetchall())


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
                    SELECT %s::bigint AS task_id,
                           COUNT(p.id) AS total,
                           COUNT(*) FILTER (WHERE COALESCE(e.status, 'pending') = 'extracted') AS extracted,
                           COUNT(*) FILTER (WHERE COALESCE(e.status, 'pending') = 'failed')    AS failed
                    FROM policy_document p
                    LEFT JOIN extraction e ON e.policy_id = p.id
                    WHERE p.task_id = %s
                ) s
                WHERE t.id = s.task_id
                RETURNING t.*
                """,
                (task_id, task_id),
            )
            return await cur.fetchone()


async def save_extraction(
    policy_id: int,
    *,
    model: str,
    facts: list[dict[str, str]],
    info_summary: str | None,
    info_points: list[dict[str, str]],
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


# ---------- 读：成果库（跨任务沉淀，仅提取成功的篇目） ----------

# 成果库「同一原文只出现一次」的实现：DISTINCT ON (source_url) 按 url 去重，
# 同一篇被多个任务抓到时保留最新一行（id 最大），重新执行任务不会覆盖/重复入库。
# 文种 policy_document 没有列，由文号前缀推导（国办发/国办函 优先于 国发/国函，再 国令）。
_LIBRARY_LATEST_CTE = """
WITH latest AS (
    SELECT DISTINCT ON (p.source_url)
        p.id, p.title, p.agency, p.doc_no, p.publish_date, p.source_url, p.created_at,
        e.extracted_at, e.info_summary,
        (SELECT COUNT(*) FROM fact_item fi WHERE fi.extraction_id = e.id) AS fact_count,
        COALESCE(jsonb_array_length(e.info_points), 0) AS point_count,
        CASE WHEN starts_with(p.doc_no, '国办发') THEN '国办发'
             WHEN starts_with(p.doc_no, '国办函') THEN '国办函'
             WHEN starts_with(p.doc_no, '国发')  THEN '国发'
             WHEN starts_with(p.doc_no, '国函')  THEN '国函'
             WHEN starts_with(p.doc_no, '国令')  THEN '国令'
        END AS doc_type
    FROM policy_document p
    JOIN extraction e ON e.policy_id = p.id
    WHERE e.status = 'extracted'
    ORDER BY p.source_url, p.id DESC
)
"""

# 列表过滤条件（agency / doc_type / year / keyword 全部可选，None = 不过滤）。
# 占位符用位置式 %s + 元组：psycopg3 不支持 %(name)s 这种 pyformat 命名占位符。
# keyword 用 strpos 做包含匹配：中文标题无大小写问题，且避开了 psycopg3 不能写字面 % 的限制
_LIBRARY_FILTER_WHERE = """
WHERE (%s::text IS NULL OR agency = %s)
  AND (%s::text IS NULL OR doc_type = %s)
  AND (%s::int IS NULL OR EXTRACT(YEAR FROM publish_date)::int = %s)
  AND (%s::text IS NULL OR strpos(title, %s) > 0)
"""


def _library_filter_params(
    agency: str | None, doc_type: str | None, year: int | None, keyword: str | None
) -> tuple:
    """过滤参数展开成位置式元组（每个条件占位符出现两次，值也要给两次）。"""
    return (agency, agency, doc_type, doc_type, year, year, keyword, keyword)


async def fetch_library_policies(
    *,
    agency: str | None,
    doc_type: str | None,
    year: int | None,
    keyword: str | None,
    limit: int,
    offset: int,
) -> list[Row]:
    """成果库列表页：按 url 去重后的提取成功篇目，发布日期倒序 + 分页。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                _LIBRARY_LATEST_CTE
                + f"SELECT * FROM latest\n{_LIBRARY_FILTER_WHERE}"
                + "ORDER BY publish_date DESC NULLS LAST, id DESC\nLIMIT %s OFFSET %s",
                _library_filter_params(agency, doc_type, year, keyword) + (limit, offset),
            )
            return await cur.fetchall()


async def count_library_policies(
    *, agency: str | None, doc_type: str | None, year: int | None, keyword: str | None
) -> int:
    """同过滤条件下的总条数（分页用）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                _LIBRARY_LATEST_CTE
                + f"SELECT COUNT(*) AS total FROM latest\n{_LIBRARY_FILTER_WHERE}",
                _library_filter_params(agency, doc_type, year, keyword),
            )
            row = await cur.fetchone()
            return int(row["total"]) if row else 0


async def fetch_library_meta() -> dict[str, Any]:
    """成果库头部统计 + 筛选下拉选项（机构 / 文种 / 年份，均在去重子集上计算）。

    返回 `{"total", "agency_count", "month_new", "agencies", "doc_types", "years"}`；
    years 的 name 是年份字符串（前端筛选项统一 name+count 形状）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                _LIBRARY_LATEST_CTE
                + """
                SELECT
                    (SELECT COUNT(*) FROM latest) AS total,
                    (SELECT COUNT(DISTINCT agency) FROM latest WHERE agency IS NOT NULL) AS agency_count,
                    (SELECT COUNT(*) FROM latest
                      WHERE extracted_at >= date_trunc('month', now())) AS month_new
                """,
            )
            stats = await cur.fetchone()

            await cur.execute(
                _LIBRARY_LATEST_CTE
                + """
                SELECT agency AS name, COUNT(*) AS count
                FROM latest
                WHERE agency IS NOT NULL
                GROUP BY agency
                ORDER BY count DESC, agency
                """,
            )
            agencies = await cur.fetchall()

            await cur.execute(
                _LIBRARY_LATEST_CTE
                + """
                SELECT doc_type AS name, COUNT(*) AS count
                FROM latest
                WHERE doc_type IS NOT NULL
                GROUP BY doc_type
                ORDER BY count DESC, doc_type
                """,
            )
            doc_types = await cur.fetchall()

            await cur.execute(
                _LIBRARY_LATEST_CTE
                + """
                SELECT EXTRACT(YEAR FROM publish_date)::int AS year, COUNT(*) AS count
                FROM latest
                WHERE publish_date IS NOT NULL
                GROUP BY year
                ORDER BY year DESC
                """,
            )
            years = [
                {"name": str(row["year"]), "count": row["count"]}
                for row in await cur.fetchall()
            ]

    return {
        "total": int(stats["total"] if stats else 0),
        "agency_count": int(stats["agency_count"] if stats else 0),
        "month_new": int(stats["month_new"] if stats else 0),
        "agencies": agencies,
        "doc_types": doc_types,
        "years": years,
    }


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
