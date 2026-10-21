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
  /** 「任意网站（无头浏览器）」的入口网址；普通来源为 null */
  browse_url: string | null
  /** 「任意网站（无头浏览器）」的自然语言浏览目标；普通来源为 null */
  browse_goal: string | null
  status: TaskStatus
  stage: string | null
  /** 本任务覆盖的流水线参数（高级设置）；null = 该项用服务端 .env 默认 */
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
  /** 提取模型（.env PIPELINE_MODELS 注册表 id）；null = 默认模型（列表第一项） */
  pipeline_model: string | null
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
  /** 发布日期来源：null=页面结构解析；'model'=模型从页面内容补提（前端标注「模型提取」） */
  publish_date_source: string | null
  /** 成文日期（标签规则/正文落款）：发布日期为空时展示兜底，不参与时间筛选 */
  signed_date: string | null
  source_url: string | null
  /** 来源目录：gongwen 公文 / bumenfile 部门文件 / gongbao 公报 / otherfile 政策解读 */
  catalog: string | null
  status: ExtractStatus
  extracted_at: string | null
  /** 已在成果库（其它任务提取成功的同一 URL）：确认面板置灰沉底、默认不勾选 */
  in_library: boolean
  /** 入库判别服务标注：true=不建议入库（确认面板置灰沉底、默认不勾选，可手动勾选）；null=未判别 */
  judge_tagged: boolean | null
  judge_prob: number | null
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
  /** 发布日期来源：null=页面结构解析；'model'=模型从页面内容补提 */
  publish_date_source: string | null
  source_url: string | null
  fetched_by: string | null
  /** 字段规则取到的站点自定义元信息（索引号 / 成文日期 / 主题分类…） */
  metadata: Record<string, string>
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
  /** 由文号里第一个括号符之前的部分推导（国发〔2026〕3号 → 国发，粤府〔2026〕1号 → 粤府）；
   *  无括号时退回 gov.cn 名单，仍推不出为 null */
  doc_type: string | null
  publish_date: string | null
  /** 发布日期来源：null=页面结构解析；'model'=模型从页面内容补提 */
  publish_date_source: string | null
  /** 成文日期（标签规则/正文落款）：发布日期为空时卡片展示兜底，不参与筛选 */
  signed_date: string | null
  summary: string | null
  fact_count: number
  point_count: number
  source_url: string | null
  /** 抓取来源（任务的 source label）；成果库跨来源混排，卡片上靠它区分是哪家站的 */
  source_name: string | null
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
  /** 「任意网站（无头浏览器）」的入口网址（该来源必填）；其它来源传 null */
  browse_url: string | null
  /** 「任意网站（无头浏览器）」的自然语言浏览目标；可为 null */
  browse_goal: string | null
  /** 本任务覆盖的流水线参数（高级设置，预填 .env 默认值）；null = 用服务端默认 */
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
  /** 提取模型（.env PIPELINE_MODELS 注册表 id）；null = 默认模型（列表第一项） */
  pipeline_model: string | null
}

/** 单个筛选项（名字 + 当前检索条件下的条数）。 */
export interface FacetOption {
  name: string
  count: number
}

/** 这个**抓取源**支持哪些筛选项——前端据此隐藏不支持的筛选项（不隐藏就是静默的空结果）。 */
export interface FacetSupports {
  agencies: boolean
  doc_types: boolean
  /** 关键词是否送进站点检索：false = 只在已翻到的列表页里比对标题（召回受翻页深度限制） */
  keyword_server_side: boolean
  /** 时间范围是否由站点过滤：false = 取回后复核（翻页范围内的条目才能被筛到） */
  date_server_side: boolean
  /** 口径说明（配置化来源：「发布机构 = 站点栏目（入口）名」这类），可直接展示 */
  note: string | null
}

/**
 * 筛选器可选值：live=实时 facet（带条数），fallback=静态回退名单。
 *
 * `source` 是**数据来源**（live/fallback），与请求参数里的 `source`（抓取源 label）不是一回事；
 * `source_label` 才回显这次问的是哪个抓取源。
 */
export interface FacetList {
  agencies: FacetOption[]
  doc_types: FacetOption[]
  source: 'live' | 'fallback'
  supports: FacetSupports
  source_label: string | null
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

/** 「高级设置 → 提取模型」下拉框的一项（后端 .env PIPELINE_MODELS 注册表，不含密钥）。 */
export interface ModelOption {
  id: string
  label: string
}

/** 生效的抓取 / 提取流水线参数（只读，来自后端 .env 的 PIPELINE_*，改配置需重启服务）。 */
export interface PipelineConfig {
  pipeline_max_policies: number
  pipeline_per_keyword_limit: number
  pipeline_extract_concurrency: number
  pipeline_max_body_chars: number
  pipeline_mcp_timeout: number
  /** 可选提取模型列表（第一项 = 默认模型） */
  models: ModelOption[]
  /** 默认提取模型 id（models[0].id；表单里的 null 会回填成它） */
  default_model: string
}

// ---------- 定时入库（Phase 11） ----------

/** 调度档位：minutely/hourly 用 every 表间隔（分钟/小时），daily/weekly 用 run_time + run_weekday */
export type ScheduleKind = 'minutely' | 'hourly' | 'daily' | 'weekly'
/** 最近一次触发结果：noop = 本轮无新增（正常跳过，不是失败） */
export type LastRunStatus = 'completed' | 'failed' | 'noop'

/** 定时任务全量状态：管理弹窗列表直接用。 */
export interface ScheduleJobDTO {
  id: number
  name: string
  source: string
  date_from: string | null
  keywords: string[]
  agencies: string[]
  doc_types: string[]
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
  /** 提取模型（.env PIPELINE_MODELS 注册表 id）；null = 默认模型（列表第一项） */
  pipeline_model: string | null
  schedule_kind: ScheduleKind
  /** minutely/hourly 的间隔（小时也折算成分钟） */
  interval_minutes: number | null
  /** daily/weekly 的运行时刻，"HH:MM"（服务器本地时间） */
  run_time: string | null
  /** weekly：0=周一 … 6=周日（对齐后端 Python weekday()） */
  run_weekday: number | null
  enabled: boolean
  next_run_at: string | null
  /** 滑动游标：下轮窗口上界（exclusive）；null = 追最新发文 */
  cursor_before: string | null
  last_run_at: string | null
  last_task_id: number | null
  last_status: LastRunStatus | null
  run_count: number
  created_at: string
}

/** 「定时自动入库」弹窗提交体：当前抓取配置快照 + 调度参数。 */
export interface ScheduleCreatePayload {
  name?: string | null
  source: string
  date_from: string | null
  keywords: string[]
  agencies: string[]
  doc_types: string[]
  pipeline_max_policies: number | null
  pipeline_per_keyword_limit: number | null
  pipeline_extract_concurrency: number | null
  pipeline_max_body_chars: number | null
  pipeline_mcp_timeout: number | null
  /** 提取模型（.env PIPELINE_MODELS 注册表 id）；null = 默认模型（列表第一项） */
  pipeline_model: string | null
  schedule_kind: ScheduleKind
  /** minutely/hourly 的间隔数值（分钟或小时，取决于 schedule_kind） */
  every: number | null
  /** daily/weekly 的运行时刻，"HH:MM" */
  run_time: string | null
  /** weekly：0=周一 … 6=周日 */
  run_weekday: number | null
}

export interface ScheduleToggled {
  job: ScheduleJobDTO
}

export interface ScheduleTriggered {
  triggered: boolean
}

export interface ScheduleDeleted {
  deleted: number
}

/** SSE 事件（`type` 字段区分）。 */
export type StreamEvent =
  | { type: 'task'; task: TaskDTO }
  | { type: 'log'; log: LogLine }
  | { type: 'done'; status: TaskStatus; task: TaskDTO }
  | { type: 'error'; message: string }

// ---------- 来源接入（Phase 15）：导入网址 + 来源管理 ----------

/** 落库状态机：draft（草案，仅试运行）→ validated → active（已启用）；disabled 可再启用 */
export type SourceStatus = 'draft' | 'validated' | 'active' | 'disabled'
/** 导入作业状态（只活在服务端进程内：重启丢进度，草案来源已落库）。queued = 批量导入等位中。 */
export type ImportJobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'
/** 导入作业阶段（与后端 `ImportPhase` 一一对应） */
export type ImportPhase = 'inspect' | 'discover' | 'generate' | 'dry_run' | 'register' | 'finished'
/** 失败性质：blocked=站点或环境不允许（不绕过访问控制）；failed=配置/站点结构不符 */
export type ImportErrorKind = 'failed' | 'blocked'

/** 来源清单的一项：内置来源（gov.cn）的 `id` 为 null 且 `builtin=true`。 */
export interface SourceDTO {
  id: number | null
  key: string
  label: string
  level: string
  organization: string | null
  status: SourceStatus
  builtin: boolean
  version: number
  /** 是否存在指纹一致的通过试运行（启用闸门就是它） */
  verified: boolean
  /** 取数引擎：html_list = 静态取数；browser_driven = 无头浏览器驱动来源（没有选择器，
   * 入口页 + 抓取时填的浏览目标）。无头只有这一条路径（html_list_rendered 已删除） */
  engine: string
  /** 入口（栏目）名；同时是「发布机构」筛选的可选值（不含检索型入口） */
  entrypoints: string[]
  supports_doc_types: boolean
  /** 关键词是否由站点检索接口执行（false = 只在已翻到的列表页里比对标题） */
  keyword_server_side: boolean
  /** 时间范围是否由站点过滤（false = 取回后复核） */
  date_server_side: boolean
  /** 站内检索入口名（空 = 没有检索入口）；用于给用户解释关键词在哪一层生效 */
  search_entrypoints: string[]
  notes: string | null
}

export interface SourceListResponse {
  items: SourceDTO[]
  /** 内置来源的 label：前端校验当前选中项用（选中项不在列表里就回退到它） */
  default_label: string
}

export interface SourceImportLog {
  seq: number
  phase: ImportPhase
  level: 'info' | 'warn' | 'error'
  message: string
  created_at: string
}

/** 一条验收判据：标准 / 实测 / 是否通过 / 硬软性（软判据失败不判失败但要标出来）。 */
export interface SourceImportCriterion {
  name: string
  threshold: string
  actual: string
  passed: boolean
  hard: boolean
  note?: string
}

/** 逐篇正文实测（验收报告 `details[]` 的一行）。 */
export interface SourceImportDetail {
  title?: string | null
  url?: string | null
  body_cjk?: number | null
  body_chars?: number | null
  sent_enders?: number | null
  body_ok?: boolean | null
  image_count?: number | null
  container_selector?: string | null
  date?: string | null
  agency?: string | null
  doc_no?: string | null
  attachments?: unknown[]
  warnings?: string[]
}

/** 验收报告（`dry_run_ingestion` 的产出，原样透传不裁剪）。 */
export interface SourceImportReport {
  status?: 'passed' | 'failed' | 'blocked'
  fingerprint?: string
  items_found?: number
  criteria?: SourceImportCriterion[]
  details?: SourceImportDetail[]
  /** 这次**没**验证什么（必须展示，不能折叠进「通过」里） */
  untested?: string[]
  /** 覆盖范围限制 */
  coverage_limits?: string[]
  attachments_probed?: unknown[]
  error?: string | null
  [key: string]: unknown
}

/** 一个候选选择器组合（报告页可点它重跑；自动用的是 `selected` 那条）。 */
export interface SourceImportCandidate {
  index: number
  page_url: string
  name: string
  /** 候选种类：region = 命名内容区域（tab 标签页/标题模块），list = 普通列表候选 */
  kind?: 'region' | 'list'
  list_selector: string
  /** 同页多列表定位：先定位作用域容器，再在其中跑 list_selector（region 候选才有） */
  scope_selector?: string | null
  /** 作用域命中多个时取第几个（0 起）；缺省 = 全部合并 */
  scope_index?: number | null
  item_count: number | null
  sample_title: string | null
  sample_url: string | null
  pagination: Record<string, unknown>
  /** 正文规则（只有被选中的候选带；其余重跑时再抓一次样本详情页） */
  detail: Record<string, unknown> | null
  /**
   * 这条候选为什么没被采用：选择器引擎执行不了，或试抓它自己的样本条目失败。
   * 「没试」和「试了不行」靠它分开——服务端会逐个候选真抓一篇样本详情页再判定。
   */
  rejected_reason?: string | null
  selected?: boolean
}

export interface SourceImportJob {
  job_id: string
  url: string
  status: ImportJobStatus
  phase: ImportPhase
  phase_text: string
  logs: SourceImportLog[]
  /** 生成的配置草案（配置校验通过后才有） */
  config: Record<string, unknown> | null
  candidates: SourceImportCandidate[]
  report: SourceImportReport | null
  /** 早期阶段（勘察/内容发现）的原始报告：作业在那一步受阻时用它说明「站点怎么说的」 */
  stage_reports: Record<string, unknown>
  registered: Record<string, unknown> | null
  error: string | null
  error_kind: ImportErrorKind | null
  /** 失败是否给出「启用无头抓取模式」按钮：静态管线拿不到内容（找不到列表/正文、JS 渲染）为 true；404/robots/5xx 为 false */
  headless_eligible?: boolean
  /** 「启用无头抓取模式」的结果：点了按钮且轻量校验通过后才填充 */
  browser_source?: SourceBrowserSourceRef | null
  started_at: string
  finished_at: string | null
}

/** 导入失败兜底自动保存的无头浏览器来源引用。 */
export interface SourceBrowserSourceRef {
  source_id?: number
  key?: string
  label?: string
  created?: boolean
}

export interface SourceImportCreated {
  job_id: string
  /** 该作业的 SSE 地址（前端订阅用） */
  stream: string
}

/** 「导入网址」作业的提交体；`base_config` + `candidate` = 候选重跑（跳过勘察与内容发现）。 */
export interface SourceImportPayload {
  url: string
  target?: string | null
  keywords?: string[]
  limit?: number
  pages?: number
  base_config?: Record<string, unknown> | null
  candidate?: SourceImportCandidate | null
}

/** 导入进度 SSE 事件。 */
export type ImportStreamEvent =
  | { type: 'phase'; job: SourceImportJob }
  | { type: 'log'; job_id: string; log: SourceImportLog }
  | { type: 'done'; status: ImportJobStatus; job: SourceImportJob }
  | { type: 'error'; message: string }

/** 一条接入执行记录（勘察 / 发现 / 试运行各一条，`report` 是当次的完整报告）。 */
export interface SourceOnboardingRun {
  id: number
  /** inspect / discover / sample / analyze / dry_run / validate */
  kind: string
  status: string
  started_at: string
  finished_at: string | null
  failure_reason: string | null
  summary: Record<string, unknown> | null
  report: SourceImportReport | null
}

/** 配置版本历史的一条（每次配置语义变化 +1）。 */
export interface SourceRevision {
  version: number
  fingerprint: string | null
  change_note: string | null
  /** agent / user / system … */
  changed_by: string | null
  created_at: string
}

export interface SourceDetail {
  source: SourceDTO
  config: Record<string, unknown>
  revisions: SourceRevision[]
  runs: SourceOnboardingRun[]
}

/** 启用结果：`register_source` 的返回 + source_id。 */
export interface SourceEnableResult {
  ok: boolean
  source_id: number
  key?: string
  label?: string
  status?: SourceStatus
  version?: number
  verification?: Record<string, unknown>
  [key: string]: unknown
}

// ---------- 批量导入 / 作业列表 / 接入记录总览（Phase 16） ----------

/** 批量导入中一条地址的立即处理结果（accepted=false 时没有作业被建出来）。 */
export interface SourceBatchImportItem {
  url: string
  job_id: string | null
  accepted: boolean
  error: string | null
}

/** 批量导入提交体：多个栏目地址 + 一组共享试抓参数。 */
export interface SourceBatchImportPayload {
  urls: string[]
  target?: string | null
  keywords?: string[]
  limit?: number
  pages?: number
}

export interface SourceBatchImportCreated {
  batch_id: string
  /** 该批的聚合 SSE 地址 */
  stream: string
  /** 逐条立即结果（保持提交行序） */
  jobs: SourceBatchImportItem[]
  accepted: string[]
  rejected: SourceBatchImportItem[]
}

/** 批量聚合 SSE 里的子作业视图。position = 排队序号（1 起），不在队列为 null。 */
export interface SourceBatchChild {
  job_id: string
  url: string
  status: ImportJobStatus
  phase: ImportPhase
  phase_text: string
  error: string | null
  error_kind: ImportErrorKind | null
  /** 同 SourceImportJob.headless_eligible */
  headless_eligible?: boolean
  /** 「启用无头抓取模式」的结果（同 SourceImportJob.browser_source） */
  browser_source?: SourceBrowserSourceRef | null
  position: number | null
  /** 进度快照已被服务端清理（持久记录见「接入记录」） */
  gone: boolean
  started_at: string
  finished_at: string | null
}

export interface SourceBatchSnapshot {
  batch_id: string
  queue: SourceBatchChild[]
  running_count: number
  pending_count: number
  all_terminal: boolean
}

/** 批量聚合 SSE 事件。 */
export type BatchStreamEvent =
  | { type: 'phase'; batch: SourceBatchSnapshot }
  | { type: 'log'; job_id: string; log: SourceImportLog }
  | { type: 'done'; batch: SourceBatchSnapshot }
  | { type: 'error'; message: string }

/** 跨来源「最近接入记录」的一行（每来源取最新一条）。 */
export interface SourceRecentRun {
  id: number
  kind: string
  status: string
  started_at: string
  finished_at: string | null
  failure_reason: string | null
  summary: Record<string, unknown> | null
  report: SourceImportReport | null
  source_key: string | null
  source_label: string
}

export interface SourceRecentRunsResponse {
  items: SourceRecentRun[]
}

/** 来源试运行提交体（真实小批量抓取，停在人工确认闸）。 */
export interface SourceTrialPayload {
  keywords?: string[]
  max_policies?: number
  /** 仅无头浏览器来源（engine=browser_driven）：这次试抓的浏览目标 */
  goal?: string | null
}

/** 「启用无头抓取模式」提交体：把导入失败的地址落成无头浏览器来源（轻量校验在服务端）。 */
export interface SourceBrowserCreatePayload {
  url: string
  label?: string | null
  /** 从哪个导入作业点进来的：保存成功后服务端把结果回写到该作业快照 */
  job_id?: string | null
}

/** 「启用无头抓取模式」返回体。 */
export interface SourceBrowserCreated {
  ok: boolean
  source_id: number
  key: string
  label: string
  engine: string
  created: boolean
  text_len: number
}

// ---------- 对话 Agent（Phase 19） ----------

/**
 * 对话流事件（对应 server/app/api/routes/agent.py::chat_stream）。
 *
 * - `token`：模型输出的文本片段；
 * - `tool` ：浏览器动作步骤（start = 开始，done = 结束并带回 ok）；只有开启「浏览网页」时才有；
 * - `done` ：正常收尾（客户端主动断开时服务端不发）；
 * - `error`：服务端错误。
 */
export interface ChatToolEvent {
  type: 'tool'
  name: string
  detail: string
  status: 'start' | 'done'
  ok: boolean
}

export type ChatStreamEvent =
  | { type: 'token'; content: string }
  | ChatToolEvent
  | { type: 'done' }
  | { type: 'error'; message: string }

/** 对话请求体（镜像 server/app/schemas/chat.py::ChatRequest）。 */
export interface ChatRequest {
  message: string
  thread_id: string
  /** 本请求是否挂载无头浏览器（模型可见 browser_* 工具） */
  browser_enabled?: boolean
}

/** 功能开关（GET /api/config/features）：可用 ≠ 已启用，前端据此决定是否显示开关。 */
export interface FeatureFlags {
  /** BROWSER_ENABLED=true 且装了 playwright（不启动 Chromium） */
  browser_available: boolean
  browser_enabled: boolean
}
