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
    # 字段来源：None=页面解析；"model"=预检/提取阶段模型补提（见 crawl_graph）
    publish_date_source: str | None
    source_name: str | None
    agency: str | None
    agency_source: str | None
    doc_no: str | None
    doc_no_source: str | None
    catalog: str | None
    fetched_by: str | None
    judge_tagged: bool | None  # 入库判别标注（Phase 13）：True=不建议入库；None=未判别
    judge_prob: float | None  # 判别为「不入库」的概率（未判别 / 拿不到为 None）
    metadata: dict[str, str]  # 字段规则取到的站点自定义元信息（索引号/成文日期…）


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
                SELECT id, task_id, title, publish_date, publish_date_source, signed_date,
                       source_url, catalog, status, extracted_at, judge_tagged, judge_prob,
                       in_library
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
    browse_url: str | None = None,
    browse_goal: str | None = None,
    pipeline_overrides: dict[str, int | str | None] | None = None,
    schedule_job_id: int | None = None,
) -> int:
    """新建抓取任务（status=pending），返回 task_id。

    `pipeline_overrides` 是页面「高级设置」提交的本任务参数（键为 `pipeline_*` 列名；
    `pipeline_model` 是模型注册表 id，str）；覆盖值必须**落库**而不是只留在请求里：
    第二段提取是确认后从库里重建的，到那时请求早已结束，只有任务行上的值还能读到。
    NULL = 用服务端 .env 默认。

    `schedule_job_id` 由定时调度器传入：标记本任务由哪个定时任务触发（跨轮去重与溯源用）。

    `browse_url` / `browse_goal` 只对「任意网站（无头浏览器）」来源有值：入口网址由
    `fetch_node` 的发现循环打开，目标交给模型筛选候选。与 overrides 同理**必须落库**——
    第二段提取是确认后重建的，那时请求早已结束。
    """
    overrides = pipeline_overrides or {}
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO crawl_task
                    (source, date_from, date_to, keywords, agencies, doc_types,
                     browse_url, browse_goal, status,
                     pipeline_max_policies, pipeline_per_keyword_limit,
                     pipeline_extract_concurrency, pipeline_max_body_chars, pipeline_mcp_timeout,
                     pipeline_model,
                     schedule_job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    source, date_from, date_to, keywords, agencies or [], doc_types or [],
                    browse_url, browse_goal,
                    overrides.get("pipeline_max_policies"),
                    overrides.get("pipeline_per_keyword_limit"),
                    overrides.get("pipeline_extract_concurrency"),
                    overrides.get("pipeline_max_body_chars"),
                    overrides.get("pipeline_mcp_timeout"),
                    overrides.get("pipeline_model"),
                    schedule_job_id,
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
                        (task_id, title, publish_date, publish_date_source,
                         source_name, source_url, agency, agency_source, doc_no, doc_no_source,
                         catalog, fetched_by, fetched_at, judge_tagged, judge_prob, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s::jsonb)
                    ON CONFLICT (task_id, source_url) DO NOTHING
                    """,
                    (
                        task_id,
                        policy["title"],
                        policy.get("publish_date"),
                        policy.get("publish_date_source"),
                        policy.get("source_name"),
                        policy["source_url"],
                        policy.get("agency"),
                        policy.get("agency_source"),
                        policy.get("doc_no"),
                        policy.get("doc_no_source"),
                        policy.get("catalog"),
                        policy.get("fetched_by"),
                        policy.get("judge_tagged"),
                        policy.get("judge_prob"),
                        json.dumps(policy.get("metadata") or {}, ensure_ascii=False),
                    ),
                )


async def update_policy_from_fulltext(
    policy_id: int,
    *,
    agency: str,
    doc_no: str,
    publish_date: date | None,
    fulltext: str | None = None,
    metadata: dict[str, str] | None = None,
) -> Row | None:
    """用全文解析出的元信息回填；解析不到的字段保持原值（不写空值进去）。

    同时把全文正文存档到 `fulltext` 列（成果库详情页「政策正文」用）；
    正文只在非空时覆盖，重试时拿到空正文不会清掉旧存档。

    `metadata` 用 JSONB 的 `||` **浅合并**：详情页字段规则取到的新键覆盖同名旧键，
    没取到的键保留（与 `COALESCE` 同口径，不拿空字典把列表轮结果抹掉）。

    返回回填后的生效行（日期/机构/文号及其来源）：调用方据此判断要不要让模型补提。
    本条解析到了某个字段时顺带把对应 `*_source` 清回 NULL——页面解析永远优先于旧模型值。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE policy_document
                SET agency       = COALESCE(NULLIF(%s, ''), agency),
                    agency_source = CASE
                        WHEN NULLIF(%s, '') IS NOT NULL THEN NULL
                        ELSE agency_source
                    END,
                    doc_no       = COALESCE(NULLIF(%s, ''), doc_no),
                    doc_no_source = CASE
                        WHEN NULLIF(%s, '') IS NOT NULL THEN NULL
                        ELSE doc_no_source
                    END,
                    publish_date = COALESCE(%s, publish_date),
                    publish_date_source = CASE
                        WHEN %s::date IS NOT NULL THEN NULL
                        ELSE publish_date_source
                    END,
                    fulltext     = COALESCE(NULLIF(%s, ''), fulltext),
                    metadata     = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                RETURNING publish_date, agency, agency_source,
                          doc_no, doc_no_source, publish_date_source
                """,
                (
                    agency,
                    agency,
                    doc_no,
                    doc_no,
                    publish_date,
                    publish_date,
                    fulltext or "",
                    json.dumps(metadata or {}, ensure_ascii=False),
                    policy_id,
                ),
            )
            return await cur.fetchone()


async def fill_fields_from_model(
    policy_id: int,
    *,
    publish_date: date | None = None,
    agency: str | None = None,
    doc_no: str | None = None,
) -> Row | None:
    """模型从页面内容补提的元信息：**只填空值**，绝不覆盖结构解析的结果。

    三个字段可一次写入（模型一次调用同时补提），各自只在原值为空时生效并标
    `*_source='model'`——前端据此标注「模型提取」，与页面解析的值区分开。
    返回更新后的生效行；调用方据 `*_source` 判断这次实际写了哪些字段。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE policy_document
                SET publish_date = COALESCE(publish_date, %s),
                    publish_date_source = CASE
                        WHEN publish_date IS NULL AND %s::date IS NOT NULL THEN 'model'
                        ELSE publish_date_source
                    END,
                    agency = COALESCE(NULLIF(agency, ''), %s),
                    agency_source = CASE
                        WHEN NULLIF(agency, '') IS NULL AND NULLIF(%s, '') IS NOT NULL THEN 'model'
                        ELSE agency_source
                    END,
                    doc_no = COALESCE(NULLIF(doc_no, ''), %s),
                    doc_no_source = CASE
                        WHEN NULLIF(doc_no, '') IS NULL AND NULLIF(%s, '') IS NOT NULL THEN 'model'
                        ELSE doc_no_source
                    END
                WHERE id = %s
                RETURNING publish_date, publish_date_source,
                          agency, agency_source, doc_no, doc_no_source
                """,
                (publish_date, publish_date, agency, agency, doc_no, doc_no, policy_id),
            )
            return await cur.fetchone()


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
#
# 文种 policy_document 没有列，由文号前缀推导，两条腿：
#   ① 通用——取文号里**第一个括号符之前**的部分（国发〔2026〕3号 → 国发，粤府〔2026〕1号 → 粤府）。
#      非 gov.cn 来源用的是自己的地方文号，只有这条腿推得出文种；前缀限 1–6 字，
#      挡住「关于xxx的通知〔2026〕3号」这种把整串当文种的情况。
#   ② 兜底——无括号的旧形态沿用 gov.cn 名单（文号写作「国令第761号」这类）。
# 前缀 r 是给正则里的 \[ 用的：PostgreSQL 的括号表达式需要反斜杠把 [ 转义成字面量，
# 而 Python 非 raw 字符串里的 \[ 会触发无效转义警告。
_LIBRARY_LATEST_CTE = r"""
WITH latest AS (
    SELECT DISTINCT ON (p.source_url)
        p.id, p.title, p.agency, p.agency_source, p.doc_no, p.doc_no_source,
        p.publish_date, p.publish_date_source,
        p.metadata->>'signed_date' AS signed_date,
        p.source_url, p.source_name, p.created_at,
        e.extracted_at, e.info_summary,
        (SELECT COUNT(*) FROM fact_item fi WHERE fi.extraction_id = e.id) AS fact_count,
        COALESCE(jsonb_array_length(e.info_points), 0) AS point_count,
        CASE
            WHEN p.doc_no ~ '^[^〔\[（【]{1,6}[〔\[（【]'
                THEN btrim(regexp_replace(p.doc_no, '[〔\[（【].*$', ''))
            WHEN starts_with(p.doc_no, '国办发') THEN '国办发'
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


# ---------- 定时入库任务（Phase 11） ----------

_PIPELINE_OVERRIDE_KEYS = (
    "pipeline_max_policies",
    "pipeline_per_keyword_limit",
    "pipeline_extract_concurrency",
    "pipeline_max_body_chars",
    "pipeline_mcp_timeout",
    "pipeline_model",
)


async def create_schedule_job(
    *,
    name: str,
    source: str,
    date_from: date | None,
    keywords: list[str],
    agencies: list[str],
    doc_types: list[str],
    pipeline_overrides: dict[str, int | str | None] | None,
    schedule_kind: str,
    interval_minutes: int | None,
    run_time: Any,  # datetime.time | None（psycopg 适配）
    run_weekday: int | None,
    next_run_at: Any,  # datetime | None
) -> int:
    """新建定时任务（enabled 默认 true），返回 job_id。配置是**快照**，之后改表单不影响它。"""
    overrides = pipeline_overrides or {}
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO schedule_job
                    (name, source, date_from, keywords, agencies, doc_types,
                     pipeline_max_policies, pipeline_per_keyword_limit,
                     pipeline_extract_concurrency, pipeline_max_body_chars, pipeline_mcp_timeout,
                     pipeline_model,
                     schedule_kind, interval_minutes, run_time, run_weekday, next_run_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    name, source, date_from, keywords, agencies or [], doc_types or [],
                    *(overrides.get(key) for key in _PIPELINE_OVERRIDE_KEYS),
                    schedule_kind, interval_minutes, run_time, run_weekday, next_run_at,
                ),
            )
            row = await cur.fetchone()
            return int(row["id"])


async def fetch_schedule_jobs(enabled_only: bool = False) -> list[Row]:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT * FROM schedule_job
                WHERE NOT %s OR enabled
                ORDER BY id
                """,
                (not enabled_only,),
            )
            return await cur.fetchall()


async def fetch_schedule_job(job_id: int) -> Row | None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM schedule_job WHERE id = %s", (job_id,))
            return await cur.fetchone()


async def fetch_due_schedule_jobs(now: Any) -> list[Row]:
    """到点的启用中任务（调度循环每 tick 扫一次）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT * FROM schedule_job
                WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= %s
                ORDER BY next_run_at
                """,
                (now,),
            )
            return await cur.fetchall()


async def set_schedule_job_next_run(job_id: int, next_run_at: Any) -> None:
    """只推进下次触发时间（调度器的「认领」动作：先占位再执行，防同 tick 重复触发）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE schedule_job SET next_run_at = %s WHERE id = %s",
                (next_run_at, job_id),
            )


async def record_schedule_run(
    job_id: int,
    *,
    status: str,
    next_run_at: Any,
    cursor_before: date | None,
    last_task_id: int | None = None,
) -> None:
    """一轮触发结束后的落库：最近结果 + 游标推进 + 计数 + 下次触发时间。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE schedule_job
                SET last_run_at = now(), last_status = %s, last_task_id = %s,
                    cursor_before = %s, next_run_at = %s, run_count = run_count + 1
                WHERE id = %s
                """,
                (status, last_task_id, cursor_before, next_run_at, job_id),
            )


async def toggle_schedule_job(job_id: int, enabled: bool, next_run_at: Any) -> Row | None:
    """启停：停用时清空 next_run_at（NULL = 不排程），启用时由调用方算好下一个触发点。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE schedule_job
                SET enabled = %s, next_run_at = %s
                WHERE id = %s
                RETURNING *
                """,
                (enabled, next_run_at if enabled else None, job_id),
            )
            return await cur.fetchone()


async def delete_schedule_job(job_id: int) -> bool:
    """删除定时任务；它触发的 crawl_task 保留（schedule_job_id 置 NULL，历史可追溯）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM schedule_job WHERE id = %s RETURNING id", (job_id,)
            )
            return await cur.fetchone() is not None


async def fetch_schedule_job_urls(job_id: int) -> set[str]:
    """本定时任务**历史所有轮次**已入库的原文链接（跨轮去重用）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT DISTINCT p.source_url
                FROM policy_document p
                JOIN crawl_task t ON t.id = p.task_id
                WHERE t.schedule_job_id = %s AND p.source_url IS NOT NULL
                """,
                (job_id,),
            )
            return {row["source_url"] for row in await cur.fetchall()}


async def fetch_library_urls() -> set[str]:
    """成果库（**全部任务**里提取成功、按 URL 去重后）的原文链接集合。

    与 fetch_schedule_job_urls（仅本定时任务历史轮次）互补：手动检索用它区分
    「新增 / 已在库」（已在库的置灰展示、不计入续抓目标），定时任务把它并入
    跨轮去重集，避免不同任务对同一篇重复提取。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT DISTINCT p.source_url
                FROM policy_document p
                JOIN extraction e ON e.policy_id = p.id
                WHERE e.status = 'extracted' AND p.source_url IS NOT NULL
                """
            )
            return {row["source_url"] for row in await cur.fetchall()}


# ---------- Phase 14：网站来源接入（来源注册表 / 变更历史 / 接入记录 / 原始证据） ----------


async def list_content_sources(status: str | None = None) -> list[Row]:
    """来源列表；`status` 给定则按状态过滤（lifespan 只装载 active）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            if status:
                await cur.execute(
                    """
                    SELECT * FROM content_source
                    WHERE status = %s
                    ORDER BY level, label
                    """,
                    (status,),
                )
            else:
                await cur.execute("SELECT * FROM content_source ORDER BY level, label")
            return list(await cur.fetchall())


async def fetch_content_source(source_id: int) -> Row | None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM content_source WHERE id = %s", (source_id,))
            return await cur.fetchone()


async def fetch_content_source_by_key(key: str) -> Row | None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM content_source WHERE key = %s", (key,))
            return await cur.fetchone()


async def save_content_source(
    *,
    config: dict[str, Any],
    label: str,
    level: str,
    organization: str | None,
    source_type: str,
    engine: str,
    status: str,
    fingerprint: str,
    entrypoint_count: int,
    notes: str | None,
    change_note: str | None = None,
    changed_by: str = "agent",
) -> dict[str, Any]:
    """新建或更新一份来源配置，返回 `{"row", "created", "version_bumped"}`。

    **版本只在配置语义真的变了（fingerprint 不同）时才 +1**：重复注册同一份配置是幂等的，
    否则每次「再点一次注册」都刷一条无意义的版本与变更记录，历史就没法看了。
    写 `content_source` 与写 `content_source_revision` 在**同一事务**里，避免配置与历史脱节。
    """
    payload = json.dumps(config, ensure_ascii=False)
    async with get_pool().connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, version, fingerprint FROM content_source WHERE key = %s",
                    (config.get("key"),),
                )
                existing = await cur.fetchone()

                if existing is None:
                    await cur.execute(
                        """
                        INSERT INTO content_source
                            (key, label, level, organization, source_type, engine, status,
                             version, config, fingerprint, entrypoint_count, notes, activated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s::jsonb, %s, %s, %s,
                                CASE WHEN %s = 'active' THEN now() END)
                        RETURNING *
                        """,
                        (
                            config.get("key"), label, level, organization, source_type, engine,
                            status, payload, fingerprint, entrypoint_count, notes, status,
                        ),
                    )
                    row = await cur.fetchone()
                    await _insert_revision(
                        cur, row["id"], 1, fingerprint, payload, change_note, changed_by
                    )
                    return {"row": row, "created": True, "version_bumped": True}

                bumped = existing["fingerprint"] != fingerprint
                version = int(existing["version"]) + 1 if bumped else int(existing["version"])
                await cur.execute(
                    """
                    UPDATE content_source
                    SET label = %s, level = %s, organization = %s, source_type = %s,
                        engine = %s, status = %s, version = %s, config = %s::jsonb,
                        fingerprint = %s, entrypoint_count = %s, notes = %s,
                        activated_at = CASE
                            WHEN %s = 'active' AND activated_at IS NULL THEN now()
                            ELSE activated_at END
                    WHERE key = %s
                    RETURNING *
                    """,
                    (
                        label, level, organization, source_type, engine, status, version,
                        payload, fingerprint, entrypoint_count, notes, status, config.get("key"),
                    ),
                )
                row = await cur.fetchone()
                if bumped:
                    await _insert_revision(
                        cur, row["id"], version, fingerprint, payload, change_note, changed_by
                    )
                return {"row": row, "created": False, "version_bumped": bumped}


async def _insert_revision(
    cur: Any,
    source_id: int,
    version: int,
    fingerprint: str,
    payload: str,
    change_note: str | None,
    changed_by: str,
) -> None:
    """写一条变更历史（调用方已开事务，这里只发 SQL）。"""
    await cur.execute(
        """
        INSERT INTO content_source_revision
            (source_id, version, fingerprint, config, change_note, changed_by)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (source_id, version) DO NOTHING
        """,
        (source_id, version, fingerprint, payload, change_note, changed_by),
    )


async def list_source_revisions(source_id: int) -> list[Row]:
    """某来源的变更历史（新版本在前）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, source_id, version, fingerprint, change_note, changed_by, created_at
                FROM content_source_revision
                WHERE source_id = %s
                ORDER BY version DESC
                """,
                (source_id,),
            )
            return list(await cur.fetchall())


async def set_content_source_status(source_id: int, status: str) -> Row | None:
    """改来源状态（启用 / 停用）；首次转 active 记 activated_at。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE content_source
                SET status = %s,
                    activated_at = CASE
                        WHEN %s = 'active' AND activated_at IS NULL THEN now()
                        ELSE activated_at END
                WHERE id = %s
                RETURNING *
                """,
                (status, status, source_id),
            )
            return await cur.fetchone()


async def delete_content_source(source_id: int) -> bool:
    """删除来源（变更历史随 FK 级联；已抓到的政策与原始证据保留，历史可追溯）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM content_source WHERE id = %s RETURNING id", (source_id,))
            return await cur.fetchone() is not None


async def create_onboarding_run(
    *,
    kind: str,
    source_key: str | None = None,
    source_id: int | None = None,
    input: dict[str, Any] | None = None,
) -> int:
    """开一条接入执行记录（status=running），返回 run_id。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO source_onboarding_run (kind, source_key, source_id, input)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (kind, source_key, source_id, json.dumps(input or {}, ensure_ascii=False)),
            )
            row = await cur.fetchone()
            return int(row["id"])


async def finish_onboarding_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> None:
    """收尾一条接入执行记录（写结论、完整报告与失败原因）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE source_onboarding_run
                SET status = %s, summary = %s::jsonb, report = %s::jsonb,
                    failure_reason = %s, finished_at = now()
                WHERE id = %s
                """,
                (
                    status,
                    json.dumps(summary or {}, ensure_ascii=False),
                    json.dumps(report or {}, ensure_ascii=False),
                    failure_reason,
                    run_id,
                ),
            )


async def fetch_onboarding_run(run_id: int) -> Row | None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM source_onboarding_run WHERE id = %s", (run_id,))
            return await cur.fetchone()


async def list_onboarding_runs(
    *, source_key: str | None = None, kinds: list[str] | None = None, limit: int = 20
) -> list[Row]:
    """接入执行记录（按时间倒序）；不传 source_key 时返回全部来源的最近若干条。

    `kinds` 过滤记录类型（inspect/dry_run/…）；跨来源「最近记录」视图用它做
    kind 筛选（Phase 16）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            conditions: list[str] = []
            params: list[Any] = []
            if source_key:
                conditions.append("source_key = %s")
                params.append(source_key)
            if kinds:
                conditions.append("kind = ANY(%s)")
                params.append(kinds)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            await cur.execute(
                f"SELECT * FROM source_onboarding_run {where} "
                "ORDER BY started_at DESC LIMIT %s",
                (*params, limit),
            )
            return list(await cur.fetchall())


# ---------- 验收凭据：通过的试运行 ----------

# 「通过的试运行」的**唯一**判定口径。启用闸门（`onboarding._latest_passing_run`）与
# 列表页的 `verified` 标志（下面那个 list_*）都必须拼这一条 SQL：两处各写一份就会漂，
# 而漂出来的分歧正是 2026-10-15 用户报的「手动停用的项目无法再次启用」——
# 列表页按指纹在**全表**里找、显示「已验收」，闸门却只在「该来源最近 20 条记录」里找，
# 凭据被后来的勘察/失败试运行挤出窗口后，两边就各说各话（按钮说能启用，点了被拒）。
_PASSING_DRY_RUN_SQL = "kind = 'dry_run' AND status = 'passed' AND report IS NOT NULL"


async def fetch_latest_passing_run(
    *, fingerprint: str | None = None, source_key: str | None = None
) -> Row | None:
    """最近一条**通过的试运行**（= 验收凭据），按时间倒序取第一条；没有则 None。

    两个条件都可选，但用途不同，语义也不同：

    - `fingerprint`：**启用闸门用的那个**。指纹是配置内容的哈希
      （`SourceConfig.config_fingerprint`），所以「指纹一致」就等于「这份配置被真跑过、
      并且通过了」——与它多久以前跑的无、与同一来源之后又写了多少条记录无关。
      **这里绝不能加条数窗口**（`ORDER BY … LIMIT 20` 那种写法）：凭据是既成事实，
      不该被后来的记录挤出视野，否则「已验收」会随时间自己退化成「没验收」。
    - `source_key`：只为**把失败原因说清楚**（不传 fingerprint = 该来源到底验收过没有）。
      用来区分「配置改过了」与「压根没跑过」——这两种情况要用户做的事不一样。
    """
    conditions = [_PASSING_DRY_RUN_SQL]
    params: list[Any] = []
    if fingerprint is not None:
        conditions.append("report->>'fingerprint' = %s")
        params.append(fingerprint)
    if source_key is not None:
        conditions.append("source_key = %s")
        params.append(source_key)
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM source_onboarding_run WHERE {' AND '.join(conditions)} "
                "ORDER BY started_at DESC LIMIT 1",
                tuple(params),
            )
            return await cur.fetchone()


async def list_passing_dry_run_fingerprints() -> set[str]:
    """全部「通过的试运行」报告里的配置指纹集合（列表页 `verified` 标志的批量口径）。

    与 `fetch_latest_passing_run` 共用 `_PASSING_DRY_RUN_SQL` 那一条判定口径，只是这里
    set 化、给列表页一次算完、消 N+1。**判定规则只许住在这两处**（都靠那个常量），
    别处不许再抄——抄一份就多一份「列表说已验收、闸门说没有」的机会。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT DISTINCT report->>'fingerprint' AS fingerprint
                FROM source_onboarding_run
                WHERE {_PASSING_DRY_RUN_SQL}
                """
            )
            return {r["fingerprint"] for r in await cur.fetchall() if r.get("fingerprint")}


async def delete_onboarding_runs(source_keys: list[str]) -> int:
    """删掉这些 key 的接入记录，返回删除条数——**只给测试收尾用**。

    用例建的来源行被删掉后，附着在它上面的勘察/试运行记录就成了没有来源的孤儿，
    留着只会在「接入记录」里刷屏。生产数据上没有任何代码该调它：**验收凭据就住在
    这些行里**，删掉等于把「这份配置验收过」这件事从历史里抹了。
    """
    if not source_keys:
        return 0
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM source_onboarding_run WHERE source_key = ANY(%s) RETURNING id",
                (list(source_keys),),
            )
            return len(await cur.fetchall())


async def insert_source_artifact(
    *,
    kind: str,
    url: str,
    run_id: int | None = None,
    source_key: str | None = None,
    task_id: int | None = None,
    policy_id: int | None = None,
    final_url: str | None = None,
    filename: str | None = None,
    ext: str | None = None,
    content_type: str | None = None,
    bytes_len: int | None = None,
    sha256: str | None = None,
    saved_path: str | None = None,
    status: str = "ok",
    error: str | None = None,
) -> int:
    """登记一条原始证据（页面 / 正文 / 附件），返回 artifact_id。

    登记≠落盘：`saved_path` 为空表示只记了元信息（例如试运行阶段只做发现与计量）。
    """
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO source_artifact
                    (run_id, source_key, task_id, policy_id, kind, url, final_url, filename,
                     ext, content_type, bytes_len, sha256, saved_path, status, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    run_id, source_key, task_id, policy_id, kind, url, final_url, filename,
                    ext, content_type, bytes_len, sha256, saved_path, status, error,
                ),
            )
            row = await cur.fetchone()
            return int(row["id"])


async def list_source_artifacts(
    *, run_id: int | None = None, task_id: int | None = None, limit: int = 100
) -> list[Row]:
    """原始证据列表（按 run 或 task 过滤）。"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            if run_id is not None:
                await cur.execute(
                    "SELECT * FROM source_artifact WHERE run_id = %s ORDER BY id LIMIT %s",
                    (run_id, limit),
                )
            elif task_id is not None:
                await cur.execute(
                    "SELECT * FROM source_artifact WHERE task_id = %s ORDER BY id LIMIT %s",
                    (task_id, limit),
                )
            else:
                await cur.execute(
                    "SELECT * FROM source_artifact ORDER BY id DESC LIMIT %s", (limit,)
                )
            return list(await cur.fetchall())
