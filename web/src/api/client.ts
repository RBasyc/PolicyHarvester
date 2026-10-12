/**
 * 后端 API 客户端：统一的 fetch 封装 + 任务进度 SSE 订阅。
 *
 * 错误约定（与后端一致）：非 2xx 时 body 为 `{"detail": "..."}`，这里抛出 ApiError，
 * 调用方直接用 `error.message` 展示（如全局 Toast）。
 */

import type {
  FacetList,
  LogLine,
  PolicyActionResponse,
  PolicyExtractionResponse,
  PolicyResultsResponse,
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

  /** 筛选器可选值（发布机构 / 文种）；失败时后端回退静态名单，不会抛错。 */
  loadFacets: (params: { q?: string; date_from?: string | null; date_to?: string | null }) => {
    const search = new URLSearchParams()
    if (params.q) search.set('q', params.q)
    if (params.date_from) search.set('date_from', params.date_from)
    if (params.date_to) search.set('date_to', params.date_to)
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
}

export interface StreamHandlers {
  onTask: (task: TaskDTO) => void
  onLog: (log: LogLine) => void
  onDone: (task: TaskDTO) => void
  onError: (message: string) => void
}

/**
 * 订阅任务进度。返回取消订阅的函数。
 *
 * 服务端用 `data: {"type": ...}` 的**未命名事件**，所以走 EventSource 的 onmessage；
 * 收到 `done` 或出错后主动关闭，避免 EventSource 自动重连。
 */
export function subscribeTaskStream(taskId: number, handlers: StreamHandlers): () => void {
  const source = new EventSource(`${BASE}/tasks/${taskId}/stream`)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }

  source.onmessage = (event: MessageEvent<string>) => {
    let payload: StreamEvent
    try {
      payload = JSON.parse(event.data) as StreamEvent
    } catch {
      return
    }

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
        close()
        break
      case 'error':
        handlers.onError(payload.message)
        close()
        break
    }
  }

  source.onerror = () => {
    if (closed) return
    close()
    handlers.onError('进度连接中断，请刷新页面或重试')
  }

  return close
}
