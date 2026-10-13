/**
 * 后端 API 契约类型（与 server/app/schemas/{task,policy}.py 一一对应）。
 * 字段名保持 snake_case，避免前后端转换带来的隐性偏差。
 */

export type TaskStatus =
  | 'pending'
  | 'running'
  /** 已检索到政策、等用户在「检索结果确认」里勾选要抓哪些（非终态） */
  | 'awaiting_confirmation'
  | 'completed'
  | 'failed'
export type ExtractStatus = 'pending' | 'extracting' | 'extracted' | 'failed'
export type ContentType = 'policy' | 'notice' | 'all' // 已弃用：仅遗留列
export type ScheduleType = 'manual' | 'daily' | 'weekly' // 已弃用：demo 不执行定时
export type LogTone = 'info' | 'success' | 'active'

/** 任务全量状态：轮询与 SSE 共用。 */
export interface TaskDTO {
  id: number
  source: string
  date_from: string | null
  date_to: string | null
  keywords: string[]
  /** 发布机构分类（bmfl，可多选） */
  agencies: string[]
  /** 文种（pcodeJiguan，可多选） */
  doc_types: string[]
  status: TaskStatus
  stage: string | null
  /** 本任务覆盖的流水线参数（高级设置）；null = 该项用服务端 .env 默认 */
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
  /** 0-100 */
  progress: number
  /** 预计剩余秒数；无可靠估算时为 null */
  eta_seconds: number | null
  total_count: number
  fetched_count: number
  extracted_count: number
  failed_count: number
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface LogLine {
  /** 已是 `[HH:MM:SS]` 形式 */
  time: string
  tone: LogTone
  message: string
}

export interface PolicyResultItem {
  id: number
  title: string
  publish_date: string | null
  source_url: string | null
  /** 来源目录：gongwen 公文 / bumenfile 部门文件 / gongbao 公报 / otherfile 政策解读 */
  catalog: string | null
  status: ExtractStatus
  extracted_at: string | null
}

export interface PolicySummary {
  total: number
  extracted: number
  extracting: number
  pending: number
  failed: number
}

export interface PolicyResultsResponse {
  items: PolicyResultItem[]
  summary: PolicySummary
}

export interface PolicyMeta {
  id: number
  source_name: string | null
  title: string
  agency: string | null
  doc_no: string | null
  publish_date: string | null
  source_url: string | null
  fetched_by: string | null
}

export interface FactItem {
  label: string
  content: string
}

export interface Concept {
  term: string
  desc: string
}

export interface ExtractionOut {
  status: ExtractStatus
  error: string | null
  retry_count: number
  model: string | null
  extracted_at: string | null
  facts: FactItem[]
  info: { summary: string | null; points: string[] }
  knowledge: { concepts: Concept[]; fields: string[]; extension: string | null }
}

export interface PolicyExtractionResponse {
  policy: PolicyMeta
  extraction: ExtractionOut
}

export interface PolicyActionResponse {
  id: number
  status: ExtractStatus
  retry_count: number
}

/** 成果库卡片：一行 = 一篇已提取政策（后端按 source_url 跨任务去重） */
export interface LibraryPolicyItem {
  id: number
  title: string
  agency: string | null
  /** 由文号前缀推导（国令/国发/国函/国办发/国办函），推导不出为 null */
  doc_type: string | null
  publish_date: string | null
  summary: string | null
  fact_count: number
  point_count: number
  source_url: string | null
}

export interface LibraryListResponse {
  items: LibraryPolicyItem[]
  total: number
  page: number
  page_size: number
}

/** 成果库头部统计 + 筛选下拉选项；years 的 name 是年份字符串。 */
export interface LibraryMeta {
  total: number
  /** 覆盖机构（distinct 发文机关数） */
  agency_count: number
  /** 本月新增（extracted_at 落在本月） */
  month_new: number
  agencies: FacetOption[]
  doc_types: FacetOption[]
  years: FacetOption[]
}

/** 从归档全文解析出的 gov.cn 元信息表（head-card 补缺用），字段只在原文出现时才有值。 */
export interface FulltextMeta {
  /** 发文机关 */
  agency: string | null
  /** 发文字号 */
  doc_no: string | null
  /** 来源 */
  source: string | null
  /** 主题分类 */
  category: string | null
  /** 公文种类 */
  doc_kind: string | null
  /** 成文日期（原文形态，如「2026年09月25日」） */
  signed_date: string | null
}

/** 成果库详情页：元信息 + 三栏提取结果 + 政策正文存档（旧数据无正文时为 null）。 */
export interface LibraryPolicyDetail {
  policy: PolicyMeta
  extraction: ExtractionOut
  fulltext: string | null
  fulltext_meta: FulltextMeta | null
}

export interface TaskCreatePayload {
  source: string
  date_from: string | null
  date_to: string | null
  keywords: string[]
  /** 发布机构分类（bmfl，可多选） */
  agencies: string[]
  /** 文种（pcodeJiguan，可多选） */
  doc_types: string[]
  /** 本任务覆盖的流水线参数（高级设置，预填 .env 默认值）；null = 用服务端默认 */
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
}

/** 单个筛选项（名字 + 当前检索条件下的条数）。 */
export interface FacetOption {
  name: string
  count: number
}

/** 筛选器可选值：live=实时 facet（带条数），fallback=静态回退名单。 */
export interface FacetList {
  agencies: FacetOption[]
  doc_types: FacetOption[]
  source: 'live' | 'fallback'
}

export interface TaskCreated {
  task_id: number
}

/** 「检索结果确认」提交体：勾选要抓取的篇目，其余在确认时被丢弃。 */
export interface TaskConfirmPayload {
  policy_ids: number[]
}

/** 删除 / 清空任务的响应体：实际删除的任务数。 */
export interface TaskDeleted {
  deleted: number
}

/** 生效的抓取 / 提取流水线参数（只读，来自后端 .env 的 PIPELINE_*，改配置需重启服务）。 */
export interface PipelineConfig {
  pipeline_max_policies: number
  pipeline_per_keyword_limit: number
  pipeline_extract_concurrency: number
  pipeline_max_body_chars: number
  pipeline_mcp_timeout: number
}

/** SSE 事件（`type` 字段区分）。 */
export type StreamEvent =
  | { type: 'task'; task: TaskDTO }
  | { type: 'log'; log: LogLine }
  | { type: 'done'; status: TaskStatus; task: TaskDTO }
  | { type: 'error'; message: string }
