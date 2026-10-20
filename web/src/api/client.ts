/**
 * 后端 API 客户端：统一的 fetch 封装 + 任务进度 SSE 订阅。
 *
 * 错误约定（与后端一致）：非 2xx 时 body 为 `{"detail": "..."}`，这里抛出 ApiError，
 * 调用方直接用 `error.message` 展示（如全局 Toast）。
 */

import type {
  BatchStreamEvent,
  ChatRequest,
  ChatStreamEvent,
  FacetList,
  FeatureFlags,
  ImportStreamEvent,
  LibraryListResponse,
  LibraryMeta,
  LibraryPolicyDetail,
  LogLine,
  PipelineConfig,
  PolicyActionResponse,
  PolicyExtractionResponse,
  PolicyResultsResponse,
  ScheduleCreatePayload,
  ScheduleDeleted,
  ScheduleJobDTO,
  ScheduleToggled,
  ScheduleTriggered,
  SourceBatchImportCreated,
  SourceBatchImportPayload,
  SourceBatchSnapshot,
  SourceEnableResult,
  SourceImportCreated,
  SourceImportJob,
  SourceImportLog,
  SourceImportPayload,
  SourceJob,
  SourceListResponse,
  SourceRecentRunsResponse,
  SourceRunsResponse,
  SourceTrialPayload,
  StreamEvent,
  TaskConfirmPayload,
  TaskCreatePayload,
  TaskCreated,
  TaskDeleted,
  TaskDTO,
} from './types'

const BASE = '/api'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // 网络层失败（后端没起、被代理断开）
    throw new ApiError('无法连接后端服务，请确认 server 已启动（默认 8000 端口）', 0)
  }

  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string' && body.detail) detail = body.detail
    } catch {
      /* 非 JSON 错误体，沿用默认文案 */
    }
    throw new ApiError(detail, response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  currentTask: () => request<TaskDTO | null>('/tasks/current'),
  task: (taskId: number) => request<TaskDTO>(`/tasks/${taskId}`),
  taskLogs: (taskId: number) => request<LogLine[]>(`/tasks/${taskId}/logs`),
  taskPolicies: (taskId: number) =>
    request<PolicyResultsResponse>(`/tasks/${taskId}/policies`),

  createTask: (payload: TaskCreatePayload) =>
    request<TaskCreated>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),

  /**
   * 筛选器可选值（发布机构 / 文种）；失败时后端回退静态名单，不会抛错。
   *
   * `source` 传当前抓取源的 label：配置化站点来源走另一条口径（发布机构 = 栏目名，
   * 没有文种），响应里的 `supports` 决定前端该隐藏哪些筛选项。
   */
  loadFacets: (params: {
    q?: string
    date_from?: string | null
    date_to?: string | null
    source?: string | null
  }) => {
    const search = new URLSearchParams()
    if (params.q) search.set('q', params.q)
    if (params.date_from) search.set('date_from', params.date_from)
    if (params.date_to) search.set('date_to', params.date_to)
    if (params.source) search.set('source', params.source)
    const qs = search.toString()
    return request<FacetList>(`/filters/facets${qs ? `?${qs}` : ''}`)
  },

  /** 确认要抓取的篇目；未勾选的会被后端丢弃，返回置为 running 后的任务快照。 */
  confirmTask: (taskId: number, policyIds: number[]) =>
    request<TaskDTO>(`/tasks/${taskId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ policy_ids: policyIds } satisfies TaskConfirmPayload),
    }),

  /** 终止运行中的任务；返回落终态后的任务快照（已是终态时后端返回 409 → ApiError）。 */
  cancelTask: (taskId: number) =>
    request<TaskDTO>(`/tasks/${taskId}/cancel`, { method: 'POST' }),

  /** 清空全部任务与结果；返回实际删除的任务数。 */
  clearTasks: () => request<TaskDeleted>('/tasks', { method: 'DELETE' }),

  policyExtraction: (policyId: number) =>
    request<PolicyExtractionResponse>(`/policies/${policyId}/extraction`),
  retryPolicy: (policyId: number) =>
    request<PolicyActionResponse>(`/policies/${policyId}/retry`, { method: 'POST' }),
  reextractPolicy: (policyId: number) =>
    request<PolicyActionResponse>(`/policies/${policyId}/reextract`, { method: 'POST' }),

  /** 成果库头部统计 + 筛选选项（机构/文种/年份）。 */
  libraryMeta: () => request<LibraryMeta>('/policies/meta'),

  /** 成果库列表：仅提取成功的篇目，按 source_url 去重，发布日期倒序分页。 */
  libraryPolicies: (params: {
    page?: number
    page_size?: number
    keyword?: string
    agency?: string
    doc_type?: string
    year?: number
  }) => {
    const search = new URLSearchParams()
    if (params.page) search.set('page', String(params.page))
    if (params.page_size) search.set('page_size', String(params.page_size))
    if (params.keyword) search.set('keyword', params.keyword)
    if (params.agency) search.set('agency', params.agency)
    if (params.doc_type) search.set('doc_type', params.doc_type)
    if (params.year) search.set('year', String(params.year))
    const qs = search.toString()
    return request<LibraryListResponse>(`/policies${qs ? `?${qs}` : ''}`)
  },

  /** 成果库详情页：元信息 + 三栏提取结果 + 政策正文存档。 */
  libraryPolicy: (policyId: number) =>
    request<LibraryPolicyDetail>(`/policies/${policyId}`),

  /** 生效的流水线参数（只读）；改配置走根目录 .env + 重启服务。 */
  pipelineConfig: () => request<PipelineConfig>('/config/pipeline'),

  /** 功能开关（目前是无头浏览器可用性；不启动 Chromium）。 */
  features: () => request<FeatureFlags>('/config/features'),

  // ---- 定时入库（Phase 11） ----
  listSchedules: () => request<ScheduleJobDTO[]>('/schedules'),
  createSchedule: (payload: ScheduleCreatePayload) =>
    request<ScheduleJobDTO>('/schedules', { method: 'POST', body: JSON.stringify(payload) }),
  /** 启用 / 停用切换，返回切换后的任务快照。 */
  toggleSchedule: (jobId: number) =>
    request<ScheduleToggled>(`/schedules/${jobId}/toggle`, { method: 'POST' }),
  /** 立即运行一轮（后台执行）；进度到抓取进度面板看。 */
  runSchedule: (jobId: number) =>
    request<ScheduleTriggered>(`/schedules/${jobId}/run`, { method: 'POST' }),
  deleteSchedule: (jobId: number) =>
    request<ScheduleDeleted>(`/schedules/${jobId}`, { method: 'DELETE' }),

  // ---- 来源接入（Phase 15）：导入网址 + 来源管理 ----
  /** 来源下拉的数据源：内置 gov.cn + content_source 的所有行（含草案/已停用）。 */
  listSources: () => request<SourceListResponse>('/sources'),
  /** 某来源的接入记录（每次勘察/试运行的完整报告，供人工复核）。 */
  sourceRuns: (sourceId: number) => request<SourceRunsResponse>(`/sources/${sourceId}/runs`),

  /** 发起一次「导入网址」作业；地址不合法（内网/非 http(s)）后端直接 400。 */
  startSourceImport: (payload: SourceImportPayload) =>
    request<SourceImportCreated>('/sources/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  sourceImportJob: (jobId: string) => request<SourceImportJob>(`/sources/import/${jobId}`),
  cancelSourceImport: (jobId: string) =>
    request<{ ok: boolean }>(`/sources/import/${jobId}/cancel`, { method: 'POST' }),

  // ---- 批量导入 / 作业列表 / 接入记录总览 / 试运行（Phase 16） ----
  /** 批量导入：N 个地址排队逐个跑（并发满排队不拒绝），逐条立即给结果。 */
  startBatchImport: (payload: SourceBatchImportPayload) =>
    request<SourceBatchImportCreated>('/sources/import/batch', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 进程内导入作业列表（倒序快照；进度详情走单作业接口）。 */
  listImportJobs: (limit = 50) => request<SourceJob[]>(`/sources/import?limit=${limit}`),
  /** 跨来源最近接入记录（每来源取最新一条）；kind 可选过滤。 */
  sourceRunsRecent: (params: { limit?: number; kind?: string } = {}) => {
    const search = new URLSearchParams()
    if (params.limit) search.set('limit', String(params.limit))
    if (params.kind) search.set('kind', params.kind)
    const qs = search.toString()
    return request<SourceRecentRunsResponse>(`/sources/runs/recent${qs ? `?${qs}` : ''}`)
  },
  /** 来源试运行：真实小批量抓取（仅 active 来源可用，否则 409）。 */
  trialSource: (sourceId: number, payload: SourceTrialPayload = {}) =>
    request<{ ok: boolean; source_id: number; task_id?: number; status?: string; note?: string }>(
      `/sources/${sourceId}/trial`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  /** 来源健康检查：逐入口真抓第 1 页。 */
  healthSource: (sourceId: number) =>
    request<{
      ok: boolean
      source_id: number
      loaded?: boolean
      entrypoints?: { name: string; url: string; reachable: boolean; http_status?: number; bytes?: number; items?: number; error?: string }[]
    }>(`/sources/${sourceId}/health`, { method: 'POST' }),
  /** 启用来源。没有通过验收时后端 409 拒绝；`force=true` 才会放行（会写进变更说明）。 */
  enableSource: (sourceId: number, payload: { force?: boolean; change_note?: string } = {}) =>
    request<SourceEnableResult>(`/sources/${sourceId}/enable`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  disableSource: (sourceId: number) =>
    request<{ ok: boolean }>(`/sources/${sourceId}/disable`, { method: 'POST' }),
  deleteSource: (sourceId: number) =>
    request<{ ok: boolean }>(`/sources/${sourceId}`, { method: 'DELETE' }),
}

/**
 * SSE 传输层：两种进度流（抓取任务的 `/tasks/{id}/stream` 与导入作业的
 * `/sources/import/{job}/stream`）用的是**同一个协议**（未命名事件 + 单行 JSON），
 * 所以订阅逻辑只写这一份。
 *
 * 服务端发的是 `data: {"type": ...}` 的**未命名事件**，因此走 EventSource 的 onmessage；
 * `onEvent` 返回 `true` 表示已收到终态，这里主动关闭连接，避免 EventSource 自动重连
 * （自动重连会重放全部历史，页面上的日志会翻倍）。
 */
function subscribeSse<E>(
  path: string,
  onEvent: (payload: E) => boolean | void,
  onError: (message: string) => void,
): () => void {
  const source = new EventSource(`${BASE}${path}`)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }

  source.onmessage = (event: MessageEvent<string>) => {
    let payload: E
    try {
      payload = JSON.parse(event.data) as E
    } catch {
      return
    }
    if (onEvent(payload) === true) close()
  }

  source.onerror = () => {
    if (closed) return
    close()
    onError('进度连接中断，请刷新页面或重试')
  }

  return close
}

export interface StreamHandlers {
  onTask: (task: TaskDTO) => void
  onLog: (log: LogLine) => void
  onDone: (task: TaskDTO) => void
  onError: (message: string) => void
}

/** 订阅抓取任务进度。返回取消订阅的函数。 */
export function subscribeTaskStream(taskId: number, handlers: StreamHandlers): () => void {
  return subscribeSse<StreamEvent>(
    `/tasks/${taskId}/stream`,
    (payload) => {
      switch (payload.type) {
        case 'task':
          handlers.onTask(payload.task)
          break
        case 'log':
          handlers.onLog(payload.log)
          break
        case 'done':
          // onDone 内部会同步最终快照，这里不再重复调用 onTask
          handlers.onDone(payload.task)
          return true
        case 'error':
          handlers.onError(payload.message)
          return true
      }
    },
    handlers.onError,
  )
}

export interface ImportStreamHandlers {
  /** 作业快照变了（阶段推进 / 产物更新）：整份替换即可 */
  onPhase: (job: SourceImportJob) => void
  onLog: (log: SourceImportLog) => void
  /** 作业进终态（done / error / cancelled）：`job` 是最终快照，报告就在里面 */
  onDone: (job: SourceImportJob) => void
  onError: (message: string) => void
}

/** 订阅导入作业进度。返回取消订阅的函数。 */
export function subscribeImportStream(
  jobId: string,
  handlers: ImportStreamHandlers,
): () => void {
  return subscribeSse<ImportStreamEvent>(
    `/sources/import/${jobId}/stream`,
    (payload) => {
      switch (payload.type) {
        case 'phase':
          handlers.onPhase(payload.job)
          break
        case 'log':
          handlers.onLog(payload.log)
          break
        case 'done':
          handlers.onDone(payload.job)
          return true
        case 'error':
          handlers.onError(payload.message)
          return true
      }
    },
    handlers.onError,
  )
}

export interface BatchStreamHandlers {
  /** 批快照变了（子作业状态/队列位置推进）：整份替换即可 */
  onPhase: (batch: SourceBatchSnapshot) => void
  onLog: (jobId: string, log: SourceImportLog) => void
  /** 全部子作业终态：`batch` 是最终快照 */
  onDone: (batch: SourceBatchSnapshot) => void
  onError: (message: string) => void
}

/** 订阅批量导入的聚合进度（单连接）。返回取消订阅的函数。 */
export function subscribeBatchStream(
  batchId: string,
  handlers: BatchStreamHandlers,
): () => void {
  return subscribeSse<BatchStreamEvent>(
    `/sources/import/batch/${batchId}/stream`,
    (payload) => {
      switch (payload.type) {
        case 'phase':
          handlers.onPhase(payload.batch)
          break
        case 'log':
          handlers.onLog(payload.job_id, payload.log)
          break
        case 'done':
          handlers.onDone(payload.batch)
          return true
        case 'error':
          handlers.onError(payload.message)
          return true
      }
    },
    handlers.onError,
  )
}

/** 一帧里如果带 `data:` 行就解析成事件；注释行（`: ping`）与半截 JSON 都返回 null。 */
function parseChatFrame(frame: string): ChatStreamEvent | null {
  const line = frame.split('\n').find((l) => l.startsWith('data:'))
  if (!line) return null
  try {
    return JSON.parse(line.slice(5).trim()) as ChatStreamEvent
  } catch {
    return null
  }
}

/**
 * 对话流：`/chat/stream` 是**带 JSON body 的 POST**，而 EventSource 只能 GET，
 * 所以这里不能用 `subscribeSse`——用 fetch + ReadableStream 手工切帧，
 * 协议与上面完全一致（`data: {...}\n\n`，夹着 `: ping` 注释行）。
 *
 * 返回的 Promise 在「收到 done / error」「用户 abort」「服务端关流」时 resolve；
 * 只有网络层失败与非 2xx 才抛 ApiError（调用方按气泡内错误展示）。
 */
export async function streamChat(
  payload: ChatRequest,
  handlers: { onEvent: (event: ChatStreamEvent) => void },
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') return // 用户点了「停止」，不是错误
    throw new ApiError('无法连接后端服务，请确认 server 已启动（默认 8000 端口）', 0)
  }

  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string' && body.detail) detail = body.detail
    } catch {
      /* 非 JSON 错误体，沿用默认文案 */
    }
    throw new ApiError(detail, response.status)
  }
  if (!response.body) throw new ApiError('当前浏览器不支持流式响应', 0)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // stream: true —— 一个中文字符的 UTF-8 字节可能被切成两半，残字节要留到下一次 decode
      buffer += decoder.decode(value, { stream: true })

      // 帧之间用空行分隔；最后一段可能是**半个帧**，留在 buffer 里等下一次 read
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const event = parseChatFrame(frame)
        if (!event) continue
        handlers.onEvent(event)
        if (event.type === 'done' || event.type === 'error') return
      }
    }
    // 服务端没发 done 就断流（进程被杀 / 代理掐断）：也按收尾处理，
    // 免得 UI 永远停在「生成中」——内容已经拿到的部分保留。
    handlers.onEvent({ type: 'done' })
  } catch (error) {
    if ((error as Error)?.name !== 'AbortError') throw error
  } finally {
    void reader.cancel().catch(() => {}) // 提前 return 时释放连接（不 cancel 会一直读到服务端超时）
  }
}
