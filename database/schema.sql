-- Agent Demo 数据库结构定义（PostgreSQL 16，基于本机 docker 容器 server-db-1 初始化）
--
-- 用法（见 README.md）：
--   psql -U kos -d agent_demo -v ON_ERROR_STOP=1 -f schema.sql
-- 脚本可重复执行：类型用 DO 块包裹，表用 IF NOT EXISTS。

-- ---------- 枚举类型 ----------

DO $$ BEGIN
    CREATE TYPE extract_status AS ENUM ('pending', 'extracting', 'extracted', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE task_status AS ENUM ('pending', 'running', 'awaiting_confirmation', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 补丁：老库的类型已存在，上面的 DO 块不会改动它，这里单独补一档。
-- `awaiting_confirmation` = 已检索到政策、等用户确认要抓哪些（见 app/pipeline/crawl_graph.py）。
-- 只加枚举值、不动任何数据，可重复执行；`ADD VALUE` 不能与其他语句同事务，故单独成句。
ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'awaiting_confirmation';

DO $$ BEGIN
    CREATE TYPE content_type AS ENUM ('policy', 'notice', 'all');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE schedule_type AS ENUM ('manual', 'daily', 'weekly');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 补丁：定时入库的周期档位（与 crawl_task 无关，schedule_job.schedule_kind 使用）。
-- minutely = 每 N 分钟；hourly = 每 N 小时（间隔统一折算成分钟存 interval_minutes）。
ALTER TYPE schedule_type ADD VALUE IF NOT EXISTS 'minutely';
ALTER TYPE schedule_type ADD VALUE IF NOT EXISTS 'hourly';

DO $$ BEGIN
    CREATE TYPE log_tone AS ENUM ('info', 'success', 'active');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---------- 表 ----------

-- 抓取任务（对应前端「抓取配置」+「抓取进度」面板）
CREATE TABLE IF NOT EXISTS crawl_task (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT '国务院政策文件库（gov.cn）',
    date_from       DATE,
    date_to         DATE,    keywords        TEXT[] NOT NULL DEFAULT '{}',
    -- gov.cn 检索筛选项（对齐官网「发布机构分类 / 文种」；API 重复参数被静默忽略，多选由服务端扇出）
    agencies        TEXT[] NOT NULL DEFAULT '{}',             -- 发布机构分类（bmfl 参数，facetMap.bmfl 的键）
    doc_types       TEXT[] NOT NULL DEFAULT '{}',             -- 文种（pcodeJiguan 参数：国令/国发/国函/国办发/国办函）
    -- 本任务覆盖的流水线参数（页面「高级设置」；NULL = 用服务端 .env 的全局默认）
    pipeline_max_policies        INTEGER,
    pipeline_per_keyword_limit   INTEGER,
    pipeline_extract_concurrency INTEGER,
    pipeline_max_body_chars      INTEGER,
    pipeline_mcp_timeout         INTEGER,
    status          task_status NOT NULL DEFAULT 'pending',
    -- 进度统计（冗余计数，便于前端直接读取，日志中的「已抓取 10/10 · 已提取 6/10」）
    total_count     INTEGER NOT NULL DEFAULT 0,
    fetched_count   INTEGER NOT NULL DEFAULT 0,
    extracted_count INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    stage           TEXT,                                     -- 当前阶段描述（如「正在调用大模型提取：第 3/10 篇」）
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 补丁：老库的 crawl_task 已存在，上面的 CREATE TABLE IF NOT EXISTS 不会改动它；
-- 这里幂等补上「gov.cn 检索筛选项」列。只加列、不动数据，可重复执行（重跑 init.sh 安全）。
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS agencies  TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS doc_types TEXT[] NOT NULL DEFAULT '{}';
-- 幂等补上「本任务覆盖的流水线参数」列（页面高级设置；可重复执行）
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_max_policies        INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_per_keyword_limit   INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_extract_concurrency INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_max_body_chars      INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_mcp_timeout         INTEGER;

-- 政策文件（对应「抓取结果」表格每行 + 抽屉头部元信息）
CREATE TABLE IF NOT EXISTS policy_document (
    id           BIGSERIAL PRIMARY KEY,
    task_id      BIGINT NOT NULL REFERENCES crawl_task(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    publish_date DATE,
    source_name  TEXT,                                        -- 来源（国务院政策文件库）
    source_url   TEXT,                                        -- 原文链接
    agency       TEXT,                                        -- 发文机关
    doc_no       TEXT,                                        -- 文号
    catalog      TEXT,                                        -- 来源目录（gongwen 公文 / bumenfile 部门文件 / gongbao 公报 / otherfile 政策解读）
    fetched_by   TEXT,                                        -- 抓取方式（如 China-Central-Policy-MCP）
    fetched_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, source_url)
);

-- 补丁：老库的 policy_document 已存在，幂等补 catalog 列。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS catalog TEXT;

-- 提取结果（与 policy_document 一对一；对应抽屉「事实 / 信息 / 知识」三区）
CREATE TABLE IF NOT EXISTS extraction (
    id                  BIGSERIAL PRIMARY KEY,
    policy_id           BIGINT NOT NULL UNIQUE REFERENCES policy_document(id) ON DELETE CASCADE,
    status              extract_status NOT NULL DEFAULT 'pending',
    error               TEXT,                                 -- 失败原因（失败时可重试）
    retry_count         INTEGER NOT NULL DEFAULT 0,
    model               TEXT,                                 -- 提取模型（如 Qwen3-235B-A22B）
    -- 信息 Information
    info_summary        TEXT,                                 -- 摘要
    info_points         JSONB NOT NULL DEFAULT '[]',          -- 要点：[{ "point": "..." }]
    -- 知识 Knowledge
    knowledge_concepts  JSONB NOT NULL DEFAULT '[]',          -- 知识点：[{ "term": "标题", "desc": "依据与做法" }]
    knowledge_fields    TEXT[] NOT NULL DEFAULT '{}',         -- 适用领域
    knowledge_extension TEXT,                                 -- 行动建议
    extracted_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 事实 Facts（明细行，计数由 COUNT 得出，对应日志「事实 12 · 信息 5 · 知识 4」）
CREATE TABLE IF NOT EXISTS fact_item (
    id            BIGSERIAL PRIMARY KEY,
    extraction_id BIGINT NOT NULL REFERENCES extraction(id) ON DELETE CASCADE,
    label         TEXT NOT NULL,                              -- 维度标签（时间 / 发文机构 / 政策动作 / 数字指标 ...）
    content       TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0                  -- 展示顺序
);

-- 任务日志（对应「抓取进度」日志流）
CREATE TABLE IF NOT EXISTS task_log (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT NOT NULL REFERENCES crawl_task(id) ON DELETE CASCADE,
    tone       log_tone NOT NULL DEFAULT 'info',              -- info / success / active
    message    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 定时入库任务（Phase 11）：抓取配置快照 + 调度参数 + 滑动游标。
-- 触发时自动全流程（跳过人工确认）：检索 → 按 URL 去重（含本任务历史轮次）→ 直接提取入库。
-- 游标语义：`cursor_before` 是下一轮窗口的上界（exclusive）。
--   - NULL        → 窗口 [date_from, 今天]，追最新发文；
--   - 非 NULL     → 窗口 [date_from, cursor_before - 1 天]，向更早回填（上轮抓满了上限才推进）。
-- 这样「窗口固定 + 上限 N + 最新优先」不会永远只抓到最新的 N 篇：抓满一轮就向更早推进一格，
-- 窗口掏空（没抓满）则回到最新，叠加跨轮 URL 去重保证不重复入库。
CREATE TABLE IF NOT EXISTS schedule_job (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    -- 抓取配置快照（创建定时任务那一刻的表单；后续改表单不影响已建任务）
    source       TEXT NOT NULL DEFAULT '国务院政策文件库（gov.cn）',
    date_from    DATE,
    keywords     TEXT[] NOT NULL DEFAULT '{}',
    agencies     TEXT[] NOT NULL DEFAULT '{}',
    doc_types    TEXT[] NOT NULL DEFAULT '{}',
    -- 流水线参数覆盖（同 crawl_task.pipeline_*；NULL = 用服务端 .env 默认）
    pipeline_max_policies        INTEGER,
    pipeline_per_keyword_limit   INTEGER,
    pipeline_extract_concurrency INTEGER,
    pipeline_max_body_chars      INTEGER,
    pipeline_mcp_timeout         INTEGER,
    -- 调度参数
    schedule_kind    schedule_type NOT NULL DEFAULT 'daily',  -- minutely / hourly / daily / weekly
    interval_minutes INTEGER,                                 -- minutely / hourly 的间隔（小时折算成分钟）
    run_time         TIME,                                    -- daily / weekly 的运行时刻（服务器本地时间）
    run_weekday      INTEGER,                                 -- weekly：0=周一 … 6=周日（对齐 Python weekday()）
    -- 运行状态
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    next_run_at  TIMESTAMPTZ,                                 -- 下次触发；NULL = 停用
    cursor_before DATE,                                       -- 滑动游标（见表头注释）
    last_run_at  TIMESTAMPTZ,
    last_task_id BIGINT REFERENCES crawl_task(id) ON DELETE SET NULL,
    last_status  TEXT,                                        -- 最近一次结果：completed / failed / noop（无新增）
    run_count    INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 补丁：老库幂等补列/补表（可重复执行，重跑 init.sh 安全）
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS schedule_job_id BIGINT REFERENCES schedule_job(id) ON DELETE SET NULL;

-- ---------- 索引 ----------

CREATE INDEX IF NOT EXISTS idx_policy_document_task  ON policy_document(task_id);
CREATE INDEX IF NOT EXISTS idx_fact_item_extraction  ON fact_item(extraction_id);
CREATE INDEX IF NOT EXISTS idx_task_log_task_time    ON task_log(task_id, created_at);
-- 定时调度：到点扫描走这个索引；跨轮 URL 去重按 (task_id, source_url) 查
CREATE INDEX IF NOT EXISTS idx_schedule_job_due      ON schedule_job(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_policy_document_url   ON policy_document(task_id, source_url);

-- ---------- updated_at 自动维护 ----------

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_crawl_task_updated') THEN
        CREATE TRIGGER trg_crawl_task_updated BEFORE UPDATE ON crawl_task
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_extraction_updated') THEN
        CREATE TRIGGER trg_extraction_updated BEFORE UPDATE ON extraction
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_schedule_job_updated') THEN
        CREATE TRIGGER trg_schedule_job_updated BEFORE UPDATE ON schedule_job
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

-- ---------- 前端「抓取结果」表格视图 ----------

CREATE OR REPLACE VIEW v_policy_results AS
SELECT
    p.id,
    p.task_id,
    p.title,
    p.publish_date,
    p.source_url,
    p.catalog,
    COALESCE(e.status, 'pending') AS status,
    e.extracted_at
FROM policy_document p
LEFT JOIN extraction e ON e.policy_id = p.id;
