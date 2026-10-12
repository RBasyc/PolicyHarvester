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

-- ---------- 索引 ----------

CREATE INDEX IF NOT EXISTS idx_policy_document_task  ON policy_document(task_id);
CREATE INDEX IF NOT EXISTS idx_fact_item_extraction  ON fact_item(extraction_id);
CREATE INDEX IF NOT EXISTS idx_task_log_task_time    ON task_log(task_id, created_at);

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
