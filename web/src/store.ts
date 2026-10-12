/**
 * 全局状态（模块级单例 store）。
 *
 * 四个面板是兄弟组件，共享同一份数据：TopBar 提交表单、ProgressPanel 展示进度、
 * ResultTable 展示结果、DetailDrawer 展示单篇详情——都由这里驱动。
 * 没有引入 Pinia：状态量小，一个模块 + `reactive/ref` 足够，也少一个依赖。
 */

import { computed, reactive, ref } from 'vue'

import { ApiError, api, subscribeTaskStream } from './api/client'
import type {
  LogLine,
  PolicyExtractionResponse,
  PolicyResultItem,
  PolicySummary,
  TaskCreatePayload,
  TaskDTO,
  TaskStatus,
} from './api/types'

/** 页面级阶段：驱动骨架屏 / 空状态 / 错误态。 */
export type Phase = 'loading' | 'idle' | 'running' | 'done' | 'error'

const EMPTY_SUMMARY: PolicySummary = {
  total: 0,
  extracted: 0,
  extracting: 0,
  pending: 0,
  failed: 0,
}

// ---------- 状态 ----------

export const task = ref<TaskDTO | null>(null)
export const logs = ref<LogLine[]>([])
export const policies = ref<PolicyResultItem[]>([])
export const summary = ref<PolicySummary>({ ...EMPTY_SUMMARY })

/** 首屏加载中（尚未确定有没有任务） */
export const booting = ref(true)
/** 「开始抓取」请求进行中 */
export const starting = ref(false)
/** 结果表格刷新中（不阻塞整页） */
export const refreshing = ref(false)

export const toast = ref<{ message: string; retry?: () => void } | null>(null)

/** 抓取配置表单（ConfigPanel 双向绑定，TopBar 提交） */
export const form = reactive<TaskCreatePayload>({
  source: '国务院政策文件库（gov.cn）',
  content_type: 'policy',
  date_from: null,
  date_to: null,
  schedule: 'manual',
  keywords: ['人工智能', '中小企业', '产业基金'],
})

export const phase = computed<Phase>(() => {
  if (booting.value) return 'loading'
  if (!task.value) return 'idle'
  if (task.value.status === 'failed') return 'error'
  if (task.value.status === 'running') return 'running'
  return 'done'
})

export const hasResult = computed(() => policies.value.length > 0)

// ---------- 内部 ----------

let stopStream: (() => void) | null = null
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string, retry?: () => void): void {
  toast.value = { message, retry }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 8000)
}

/** 组件里的一般性提示（表单导入成功/失败等）。 */
export function notify(message: string): void {
  showToast(message)
}

export function dismissToast(): void {
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = null
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error)
}

async function refreshPolicies(taskId: number): Promise<void> {
  refreshing.value = true
  try {
    const data = await api.taskPolicies(taskId)
    policies.value = data.items
    summary.value = data.summary
  } catch (error) {
    showToast(`结果列表加载失败：${errorText(error)}`)
  } finally {
    refreshing.value = false
  }
}

function stopSubscription(): void {
  stopStream?.()
  stopStream = null
}

// ---------- 动作 ----------

/** 首屏：拉当前任务 + 日志 + 结果；没有任务则进入空状态。 */
export async function bootstrap(): Promise<void> {
  booting.value = true
  try {
    const current = await api.currentTask()
    task.value = current
    if (current) {
      logs.value = await api.taskLogs(current.id)
      await refreshPolicies(current.id)
      if (current.status === 'running') subscribe(current.id)
    } else {
      logs.value = []
      policies.value = []
      summary.value = { ...EMPTY_SUMMARY }
    }
  } catch (error) {
    showToast(`初始化失败：${errorText(error)}`)
  } finally {
    booting.value = false
  }
}

/** 订阅任务进度（SSE）。服务端会先补发全部历史日志，所以这里清空本地日志。 */
export function subscribe(taskId: number): void {
  stopSubscription()
  logs.value = []
  let lastCounts = ''

  stopStream = subscribeTaskStream(taskId, {
    onTask: (next) => {
      task.value = next
      // 计数变了才刷新结果表格，避免每次快照变化都打一次接口
      const counts = [next.total_count, next.fetched_count, next.extracted_count, next.failed_count]
        .join('/')
      if (counts !== lastCounts) {
        lastCounts = counts
        void refreshPolicies(next.id)
      }
    },
    onLog: (line) => {
      logs.value = [...logs.value, line]
    },
    onDone: (final) => {
      task.value = final
      void refreshPolicies(final.id)
      if (final.status === 'failed') {
        showToast(`抓取失败：${final.stage ?? '未知原因'}`, () => void startTask())
      }
    },
    onError: (message) => showToast(message, () => void startTask()),
  })
}

/** 点「开始抓取」：提交表单 → 立刻返回 task_id → 订阅进度。 */
export async function startTask(): Promise<void> {
  if (starting.value) return
  starting.value = true
  dismissToast()
  try {
    // 日期输入框清空后拿到的是空串，后端要的是 null
    const { task_id } = await api.createTask({
      ...form,
      date_from: form.date_from || null,
      date_to: form.date_to || null,
      keywords: [...form.keywords],
    })
    task.value = await api.task(task_id)
    policies.value = []
    summary.value = { ...EMPTY_SUMMARY }
    subscribe(task_id)
  } catch (error) {
    showToast(`抓取失败：${errorText(error)}`, () => void startTask())
  } finally {
    starting.value = false
  }
}

/** 失败行重试（后端会立刻重跑这一篇）。 */
export async function retryPolicy(policyId: number): Promise<void> {
  try {
    await api.retryPolicy(policyId)
    if (task.value) subscribe(task.value.id)
  } catch (error) {
    showToast(`重试失败：${errorText(error)}`)
  }
}

/** 抽屉里的「重新提取」。 */
export async function reextractPolicy(policyId: number): Promise<void> {
  try {
    await api.reextractPolicy(policyId)
    if (task.value) subscribe(task.value.id)
  } catch (error) {
    showToast(`重新提取失败：${errorText(error)}`)
  }
}

/** 拉单篇详情（抽屉用）。 */
export async function loadExtraction(policyId: number): Promise<PolicyExtractionResponse> {
  return api.policyExtraction(policyId)
}

export async function refreshLogs(): Promise<void> {
  if (task.value) logs.value = await api.taskLogs(task.value.id)
}

export function isRunning(status: TaskStatus | undefined): boolean {
  return status === 'running'
}
