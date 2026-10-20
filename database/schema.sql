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

-- Phase 14：网站来源接入
-- 来源层级：与前端「来源」列表的分类口径一致（国家级 / 省 / 市 / 县 / 其他）。
DO $$ BEGIN
    CREATE TYPE source_level AS ENUM ('national', 'province', 'city', 'county', 'other');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 来源状态机：draft（仅试运行）→ validated（试运行通过）→ active（已注册、抓取任务可选）
--                                     ↘ disabled（人工停用，可再启用）
DO $$ BEGIN
    CREATE TYPE content_source_status AS ENUM ('draft', 'validated', 'active', 'disabled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 接入执行状态：running（进行中）/ passed（通过）/ failed（失败）/ blocked（受阻，需补能力）
DO $$ BEGIN
    CREATE TYPE onboarding_status AS ENUM ('running', 'passed', 'failed', 'blocked');
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
    -- 「任意网站（无头浏览器）」来源的入口与目标（NULL = 普通站点检索任务）
    browse_url      TEXT,                                     -- 浏览器发现的入口网址（http(s)）
    browse_goal     TEXT,                                     -- 自然语言目标（如「找最近三个月新能源政策」）
    -- 本任务覆盖的流水线参数（页面「高级设置」；NULL = 用服务端 .env 的全局默认）
    pipeline_max_policies        INTEGER,
    pipeline_per_keyword_limit   INTEGER,
    pipeline_extract_concurrency INTEGER,
    pipeline_max_body_chars      INTEGER,
    pipeline_mcp_timeout         INTEGER,
    pipeline_model               TEXT,                             -- 提取模型（.env PIPELINE_MODELS 注册表 id；NULL = 默认模型）
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
-- 幂等补上「任意网站（无头浏览器）」入口 / 目标列（老库可重复执行）
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS browse_url  TEXT;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS browse_goal TEXT;
-- 幂等补上「本任务覆盖的流水线参数」列（页面高级设置；可重复执行）
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_max_policies        INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_per_keyword_limit   INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_extract_concurrency INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_max_body_chars      INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_mcp_timeout         INTEGER;
ALTER TABLE crawl_task ADD COLUMN IF NOT EXISTS pipeline_model               TEXT;

-- 政策文件（对应「抓取结果」表格每行 + 抽屉头部元信息）
CREATE TABLE IF NOT EXISTS policy_document (
    id           BIGSERIAL PRIMARY KEY,
    task_id      BIGINT NOT NULL REFERENCES crawl_task(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    publish_date DATE,
    publish_date_source TEXT,                                 -- 日期来源：NULL=页面解析，'model'=模型从页面内容补提
    source_name  TEXT,                                        -- 来源（国务院政策文件库）
    source_url   TEXT,                                        -- 原文链接
    agency       TEXT,                                        -- 发文机关
    doc_no       TEXT,                                        -- 文号
    catalog      TEXT,                                        -- 来源目录（gongwen 公文 / bumenfile 部门文件 / gongbao 公报 / otherfile 政策解读）
    fetched_by   TEXT,                                        -- 抓取方式（如 China-Central-Policy-MCP）
    fetched_at   TIMESTAMPTZ,
    fulltext     TEXT,                                        -- 政策正文存档（提取阶段落库，供成果库「政策正文」展示）
    judge_tagged BOOLEAN,                                     -- 入库判别标注（Phase 13）：NULL=未判别，TRUE=不建议入库
    judge_prob   REAL,                                        -- 判别为「不入库」的概率（未判别 / 拿不到为 NULL）
    metadata     JSONB NOT NULL DEFAULT '{}',                 -- 字段规则取到的站点自定义元信息（索引号/成文日期/主题分类…）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, source_url)
);

-- 补丁：老库的 policy_document 已存在，幂等补 catalog 列。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS catalog TEXT;
-- 补丁：成果库「政策正文」需要全文存档，提取阶段落库（旧行为只回填元信息不落正文）。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS fulltext TEXT;
-- 补丁：Phase 13 入库判别——检索阶段判别服务（judge）对候选篇目的标注结果。
-- judge_tagged：NULL = 未判别（判别关闭 / 服务不可用 fail-open / 已在库未判 / 历史行），
--               TRUE = 不建议入库（手动任务仅提示；定时任务自动剔除）。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS judge_tagged BOOLEAN;
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS judge_prob   REAL;
-- 补丁：配置化来源的字段规则（detail.fields / entrypoint.item_fields）取到的自定义元信息。
-- 用 JSONB 而不是加列：站点字段名千差万别（索引号 / 成文日期 / 主题分类 / 发文字号…），
-- 逐字段加列会让每接一个站都动 schema；标准字段仍走 agency / doc_no / publish_date 列。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
-- 补丁：发布日期来源。NULL = 抓取时从页面结构解析（列表/详情规则）；
-- 'model' = 结构解析没拿到，提取阶段由模型从页面内容补提（前端据此标注「模型提取」）。
-- 单独一列而不是塞 metadata：表格/详情都要读它，且它约束的是 publish_date 这一标准列的可信度。
ALTER TABLE policy_document ADD COLUMN IF NOT EXISTS publish_date_source TEXT;

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
    pipeline_model               TEXT,
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
-- 补丁：schedule_job 补「提取模型」覆盖列（老库已建过 schedule_job 时 CREATE TABLE 不会补列）
ALTER TABLE schedule_job ADD COLUMN IF NOT EXISTS pipeline_model TEXT;

-- ---------- Phase 14：网站来源接入 ----------

-- 来源注册表：一份 SourceConfig 一行（config 列存完整 JSONB）。
-- 抓取任务通过 crawl_task.source（展示名 label）解析到来源，因此 label 必须唯一。
CREATE TABLE IF NOT EXISTS content_source (
    id               BIGSERIAL PRIMARY KEY,
    key              TEXT NOT NULL UNIQUE,      -- 稳定标识（SourceConfig.key，配置化来源的注册表键）
    label            TEXT NOT NULL UNIQUE,      -- 展示名；写进 crawl_task.source，按此解析适配器
    level            source_level NOT NULL DEFAULT 'other',
    organization     TEXT,
    source_type      TEXT NOT NULL DEFAULT 'government_portal',  -- government_portal / open_api / rss / mixed
    engine           TEXT NOT NULL DEFAULT 'html_list',
    status           content_source_status NOT NULL DEFAULT 'draft',
    version          INTEGER NOT NULL DEFAULT 1,
    config           JSONB NOT NULL,            -- 完整 SourceConfig（app/sources/config.py）
    fingerprint      TEXT,                      -- SourceConfig.config_fingerprint()：判断内容是否真变了
    entrypoint_count INTEGER NOT NULL DEFAULT 0, -- 冗余字段：来源列表页免解析 JSONB
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at     TIMESTAMPTZ                -- 首次置为 active 的时间（正式启用留痕）
);

-- 配置变更历史：每次注册 / 更新写一条，支撑「可追溯、可重跑」（哪一版、谁改的、改了什么）。
CREATE TABLE IF NOT EXISTS content_source_revision (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES content_source(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    fingerprint TEXT,
    config      JSONB NOT NULL,                 -- 该版本的完整配置快照（对比两版差异用）
    change_note TEXT,
    changed_by  TEXT,                           -- 'agent' / 'user' / 'system'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, version)
);

-- 接入执行记录：勘察 / 取样 / 试运行 / 验收各留一条，验收报告存 report。
-- 与 crawl_task 的区别：这里记的是**接入过程**（能不能接、接得对不对），
-- crawl_task 记的是**生产抓取**（这次抓了哪些政策）。
CREATE TABLE IF NOT EXISTS source_onboarding_run (
    id             BIGSERIAL PRIMARY KEY,
    source_id      BIGINT REFERENCES content_source(id) ON DELETE SET NULL,  -- 勘察阶段可能还没有来源行
    source_key     TEXT,                        -- 冗余：来源行删除后仍可追溯这次接入针对哪个 key
    kind           TEXT NOT NULL,               -- inspect / discover / sample / analyze / dry_run / validate
    status         onboarding_status NOT NULL DEFAULT 'running',
    input          JSONB,                       -- 本次输入（URL、目标、样例范围等）
    summary        JSONB,                       -- 阶段结论（精简，给对话回复用）
    report         JSONB,                       -- 完整报告（勘察报告 / 验收报告）
    failure_reason TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);

-- 原始证据归档：抓回的页面 / 正文 / 附件都记一条（含 sha256 与落盘路径），
-- 满足「原始文本与文件、来源标识、原始 URL、抓取时间、元数据、版本信息」的留存要求。
CREATE TABLE IF NOT EXISTS source_artifact (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT REFERENCES source_onboarding_run(id) ON DELETE SET NULL,
    source_key   TEXT,
    task_id      BIGINT REFERENCES crawl_task(id) ON DELETE SET NULL,   -- 生产抓取归属的任务（试运行为 NULL）
    policy_id    BIGINT REFERENCES policy_document(id) ON DELETE CASCADE, -- 关联到落库的政策行（可 null）
    kind         TEXT NOT NULL,                 -- page / fulltext / attachment
    url          TEXT NOT NULL,
    final_url    TEXT,                          -- 重定向后的地址
    filename     TEXT,
    ext          TEXT,
    content_type TEXT,
    bytes_len    INTEGER,
    sha256       TEXT,                          -- 内容哈希：重复内容检测与完整性校验
    saved_path   TEXT,                          -- 落盘相对路径；NULL = 只登记未落盘
    status       TEXT NOT NULL DEFAULT 'ok',    -- ok / truncated / failed
    error        TEXT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 索引 ----------

CREATE INDEX IF NOT EXISTS idx_policy_document_task  ON policy_document(task_id);
CREATE INDEX IF NOT EXISTS idx_policy_document_date   ON policy_document(publish_date);
CREATE INDEX IF NOT EXISTS idx_fact_item_extraction  ON fact_item(extraction_id);
CREATE INDEX IF NOT EXISTS idx_task_log_task_time    ON task_log(task_id, created_at);
-- 定时调度：到点扫描走这个索引；跨轮 URL 去重按 (task_id, source_url) 查
CREATE INDEX IF NOT EXISTS idx_schedule_job_due      ON schedule_job(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_policy_document_url   ON policy_document(task_id, source_url);
-- Phase 13：v_policy_results.in_library 的相关子查询只按 source_url 探测（前导列是 task_id 用不上）
CREATE INDEX IF NOT EXISTS idx_policy_document_url_only ON policy_document(source_url);
-- Phase 14：来源注册表按状态装载（lifespan 只取 active），变更历史与接入记录按来源倒序翻
CREATE INDEX IF NOT EXISTS idx_content_source_status    ON content_source(status);
CREATE INDEX IF NOT EXISTS idx_source_revision_source   ON content_source_revision(source_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_onboarding_run_source    ON source_onboarding_run(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_onboarding_run_key       ON source_onboarding_run(source_key, started_at DESC);
-- 验收凭据（启用闸门 + 列表页 verified 标志）按报告里的指纹在**全表**里找通过试运行，
-- 不带条数窗口（见 repositories._PASSING_DRY_RUN_SQL）：这条部分索引专门服务那次查询
CREATE INDEX IF NOT EXISTS idx_onboarding_run_passing_fp ON source_onboarding_run((report->>'fingerprint'))
    WHERE kind = 'dry_run' AND status = 'passed';
CREATE INDEX IF NOT EXISTS idx_source_artifact_run      ON source_artifact(run_id);
CREATE INDEX IF NOT EXISTS idx_source_artifact_url      ON source_artifact(url);

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
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_content_source_updated') THEN
        CREATE TRIGGER trg_content_source_updated BEFORE UPDATE ON content_source
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

-- ---------- 前端「抓取结果」表格视图 ----------

-- 注意：新列只能**追加在列尾**（CREATE OR REPLACE VIEW 的限制），不要插到中间。
CREATE OR REPLACE VIEW v_policy_results AS
SELECT
    p.id,
    p.task_id,
    p.title,
    p.publish_date,
    p.source_url,
    p.catalog,
    COALESCE(e.status, 'pending') AS status,
    e.extracted_at,
    p.judge_tagged,
    p.judge_prob,
    -- 已在成果库（**其它任务**已提取成功的同一 URL）：确认面板据此置灰沉底、默认不勾选。
    -- 排除本任务自己的行，否则提取中途全表都会变成「已在库」。
    EXISTS (
        SELECT 1
        FROM policy_document p2
        JOIN extraction e2 ON e2.policy_id = p2.id
        WHERE e2.status = 'extracted'
          AND p2.source_url = p.source_url
          AND p2.task_id <> p.task_id
    ) AS in_library,
    -- 追加在列尾：CREATE OR REPLACE VIEW 只允许在末尾加列
    p.publish_date_source
FROM policy_document p
LEFT JOIN extraction e ON e.policy_id = p.id;
