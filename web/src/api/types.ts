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

export interface TaskCreatePayload {
  source: string
  date_from: string | null
  date_to: string | null
  keywords: string[]
  /** 发布机构分类（bmfl，可多选） */
  agencies: string[]
  /** 文种（pcodeJiguan，可多选） */
  doc_types: string[]
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

/** SSE 事件（`type` 字段区分）。 */
export type StreamEvent =
  | { type: 'task'; task: TaskDTO }
  | { type: 'log'; log: LogLine }
  | { type: 'done'; status: TaskStatus; task: TaskDTO }
  | { type: 'error'; message: string }
