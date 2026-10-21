/**
 * 来源导入状态（Phase 16）：单条导入向导 + 批量导入队列。
 *
 * 放模块级（而不是组件里）的原因：SourcesPage 用 tab 组织三个模块，切换 tab
 * 若用 v-if 会销毁组件、丢掉跑到一半的向导状态；放这里则天然存活。
 * 路由离开 /sources（组件卸载）时 SSE 连接由组件停掉，但状态保留——回来时
 * `syncImportsOnMount` 重新对表：在跑的作业重订阅流，已完成的作业补最终快照。
 *
 * 持久留痕永远在服务端 `source_onboarding_run`；这里的只是进程内进度的镜像。
 */

import { computed, reactive, ref } from 'vue'

import { ApiError, api, subscribeBatchStream, subscribeImportStream } from '../api/client'
import type {
  SourceBatchImportPayload,
  SourceImportCandidate,
  SourceImportJob,
  SourceImportLog,
  SourceRecentRun,
  SourceStatus,
} from '../api/types'
import { loadSources, notify, notifySuccess } from '../store'

function text(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error)
}

/** `created_at` 是带时区的 ISO，日志里只留时分秒。 */
export function clock(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '--:--:--'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export const LOG_LEVEL_TONE: Record<string, string> = { info: 'info', warn: 'warn', error: 'error' }

export const STATUS_LABEL: Record<SourceStatus, string> = {
  draft: '草案',
  validated: '已校验',
  active: '已启用',
  disabled: '已停用',
}

const TERMINAL = ['done', 'error', 'cancelled']

// ---------- 启用（列表与向导报告页共用） ----------

export const busySourceId = ref<number | null>(null)

/** 启用/强制启用一个来源：与来源列表同一套提示与刷新。返回是否成功（调用方决定是否清向导）。 */
export async function enableSourceById(sourceId: number, label: string, force = false): Promise<boolean> {
  if (busySourceId.value != null) return false
  busySourceId.value = sourceId
  try {
    await api.enableSource(sourceId, { force })
    notifySuccess(
      force
        ? `已强制启用「${label}」——未经验收强制启用已写入变更说明`
        : `已启用「${label}」，可在抓取配置里选它作为来源`,
    )
    await loadSources()
    void refreshRecentRuns()
    return true
  } catch (error) {
    notify(`启用失败：${text(error)}`)
    return false
  } finally {
    busySourceId.value = null
  }
}

// ---------- 单条导入向导 ----------

type ImportStep = 'form' | 'running' | 'report'

export const wizard = reactive({
  step: 'form' as ImportStep,
  draft: { url: '', target: '', keywords: '', limit: 3, pages: 1 },
  starting: false,
  formError: null as string | null,
  job: null as SourceImportJob | null,
  jobLogs: [] as SourceImportLog[],
  streamError: null as string | null,
  selectedCandidateIndex: null as number | null,
})

let wizardStopStream: (() => void) | null = null

/** 报告页里的启用目标：作业登记出来的那条来源（草案）。 */
export const registeredId = computed(() => {
  const value = wizard.job?.registered?.source_id
  return typeof value === 'number' ? value : null
})
export const registeredLabel = computed(() => {
  const value = wizard.job?.registered?.label
  return typeof value === 'string' ? value : wizard.job?.url || '该来源'
})
export const registeredStatus = computed<SourceStatus | null>(() => {
  const value = wizard.job?.registered?.status
  return typeof value === 'string' ? (value as SourceStatus) : null
})
export const reportPassed = computed(() => wizard.job?.report?.status === 'passed')
/** 报告页的「启用该来源」：登记出来了、还没启用、且是**通过验收**的那一份 */
export const canEnableRegistered = computed(
  () => registeredId.value != null && registeredStatus.value !== 'active' && reportPassed.value,
)

export const selectedCandidate = computed<SourceImportCandidate | null>(() => {
  const list = wizard.job?.candidates ?? []
  return list.find((c) => c.index === wizard.selectedCandidateIndex) ?? null
})
export const canRerunCandidate = computed(() => {
  const candidate = selectedCandidate.value
  return (
    wizard.step === 'report' && candidate != null && !candidate.selected && !wizard.starting
  )
})

function parseKeywords(raw: string): string[] {
  return raw
    .split(/[,，、;；\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function requestUrl(): string {
  return wizard.draft.url.trim()
}

/** 前端先拦一道明显的非法输入；服务端那道（公网 http/https、非内网）才是权威。 */
export function validateWizardUrl(): string | null {
  const url = requestUrl()
  if (!url) return '请填写要接入的栏目地址'
  if (!/^https?:\/\//i.test(url)) return '地址需以 http:// 或 https:// 开头'
  try {
    new URL(url)
  } catch {
    return '地址格式不合法'
  }
  return null
}

function subscribeWizard(jobId: string) {
  stopWizardStream()
  wizard.jobLogs = []
  // 服务端订阅时先补发全部历史日志，所以这里清空本地日志即可（与任务进度流同一套）
  wizardStopStream = subscribeImportStream(jobId, {
    onPhase: (next) => {
      wizard.job = next
    },
    onLog: (line) => {
      wizard.jobLogs = [...wizard.jobLogs, line]
    },
    onDone: (final) => {
      wizard.job = final
      wizard.step = 'report'
      wizardStopStream = null
      wizard.selectedCandidateIndex = final.candidates.find((c) => c.selected)?.index ?? null
      // 草案来源已落库：列表要跟着更新（含 builtin 的 verified 状态）
      void loadSources()
    },
    onError: (message) => {
      wizard.streamError = message
      wizardStopStream = null
    },
  })
}

export function stopWizardStream() {
  wizardStopStream?.()
  wizardStopStream = null
}

export async function startWizardImport() {
  const problem = validateWizardUrl()
  if (problem) {
    wizard.formError = problem
    return
  }
  await submitWizardImport({
    url: requestUrl(),
    target: wizard.draft.target.trim() || null,
    keywords: parseKeywords(wizard.draft.keywords),
    limit: Number(wizard.draft.limit) || 3,
    pages: Number(wizard.draft.pages) || 1,
  })
}

/** 提交一次导入作业（首次接入与候选重跑走同一条路）。 */
export async function submitWizardImport(payload: {
  url: string
  target?: string | null
  keywords?: string[]
  limit?: number
  pages?: number
  base_config?: Record<string, unknown> | null
  candidate?: SourceImportCandidate | null
}) {
  if (wizard.starting) return
  wizard.starting = true
  stopWizardStream()
  wizard.job = null
  wizard.jobLogs = []
  wizard.streamError = null
  wizard.formError = null
  wizard.selectedCandidateIndex = null
  wizard.step = 'running'
  try {
    const { job_id } = await api.startSourceImport(payload)
    subscribeWizard(job_id)
  } catch (error) {
    // 400 = 地址不可用（内网/非 http）/ 429 = 冷却或并发已满；此时没有作业被建出来
    wizard.formError = text(error)
    wizard.step = 'form'
  } finally {
    wizard.starting = false
  }
}

/** 用选中的候选重跑：沿用同一份配置骨架，只替换入口与正文规则。 */
export async function rerunSelectedCandidate() {
  const candidate = selectedCandidate.value
  if (!candidate) return
  // 骨架可能没有（上一次在「选候选」那一步就失败）：服务端会按同一套规则重建，
  // 所以这里不再拦——否则失败页上的候选清单成了摆设，点哪个都没反应。
  await submitWizardImport({
    url: requestUrl(),
    target: wizard.draft.target.trim() || null,
    keywords: parseKeywords(wizard.draft.keywords),
    limit: Number(wizard.draft.limit) || 3,
    pages: Number(wizard.draft.pages) || 1,
    base_config: wizard.job?.config ?? null,
    candidate,
  })
}

export async function cancelWizardImport() {
  const current = wizard.job
  if (!current || (current.status !== 'running' && current.status !== 'queued')) return
  try {
    await api.cancelSourceImport(current.job_id)
    notifySuccess('已请求取消——等当前这一步收尾后作业结束，已抓证据仍保留')
  } catch (error) {
    notify(`取消失败：${text(error)}`)
  }
}

/** 报告页「启用该来源」：启用成功后把向导重置回表单页，方便接着导入下一条。 */
export async function enableRegistered() {
  const id = registeredId.value
  if (id == null) return
  const ok = await enableSourceById(id, registeredLabel.value)
  if (ok) resetWizard()
}

/** 停用一个来源（列表卡片开关与详情页共用）。返回是否成功。 */
export async function disableSourceById(sourceId: number, label: string): Promise<boolean> {
  if (busySourceId.value != null) return false
  busySourceId.value = sourceId
  try {
    await api.disableSource(sourceId)
    notifySuccess(`已停用「${label}」——已抓的任务与成果不受影响`)
    await loadSources()
    void refreshRecentRuns()
    return true
  } catch (error) {
    notify(`停用失败：${text(error)}`)
    return false
  } finally {
    busySourceId.value = null
  }
}

/** 删除一个来源（列表与详情页共用；调用方负责二次确认与事后导航）。返回是否成功。 */
export async function deleteSourceById(sourceId: number, label: string): Promise<boolean> {
  if (busySourceId.value != null) return false
  busySourceId.value = sourceId
  try {
    await api.deleteSource(sourceId)
    notifySuccess(`已删除来源「${label}」`)
    await loadSources()
    void refreshRecentRuns()
    return true
  } catch (error) {
    notify(`删除失败：${text(error)}`)
    return false
  } finally {
    busySourceId.value = null
  }
}

export function resetWizard() {
  stopWizardStream()
  wizard.step = 'form'
  wizard.job = null
  wizard.jobLogs = []
  wizard.streamError = null
  wizard.formError = null
  wizard.selectedCandidateIndex = null
}

// ---------- 批量导入 ----------

export const batch = reactive({
  batchId: null as string | null,
  snapshot: null as import('../api/types').SourceBatchSnapshot | null,
  starting: false,
  formError: null as string | null,
  streamError: null as string | null,
  draft: { urls: '', target: '', keywords: '', limit: 3, pages: 1 },
  /** 展开查看详情的子作业（整份快照含报告/候选，走单作业接口拉一次） */
  expandedJobId: null as string | null,
  expandedJob: null as SourceImportJob | null,
  expandedLogs: [] as SourceImportLog[],
  /** 每个子作业的最新一行日志（聚合 SSE 的 log 事件攒出来的） */
  lastLogs: {} as Record<string, SourceImportLog>,
})

let batchStopStream: (() => void) | null = null

/**
 * 批量展开行里已完成的善后动作（目前只有「启用」；失败兜底改由服务端自动保存
 * 无头浏览器来源，不再有需要前端手动记的行内动作），按 job_id 记录：
 * 行内据此把按钮换成结果提示。作业快照（expandedJob）重拉不会丢它——
 * 服务端作业是终态，注册状态本来就不写回作业本身。
 */
export const handledJobs = reactive<Record<string, 'enabled'>>({})

/** 每来源最近一次试运行（dry_run）：列表卡片的摘要行用，按 source_key 映射。 */
export const recentDryRuns = ref<Record<string, SourceRecentRun>>({})

export async function refreshRecentRuns(): Promise<void> {
  try {
    const response = await api.sourceRunsRecent({ kind: 'dry_run', limit: 50 })
    const map: Record<string, SourceRecentRun> = {}
    for (const item of response.items) {
      if (item.source_key) map[item.source_key] = item
    }
    recentDryRuns.value = map
  } catch {
    /* 摘要行只是辅助视图，失败静默 */
  }
}

/** 解析批量 textarea：去空行 / # 注释行 / 重复行。 */
export function parseBatchUrls(raw: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const line of raw.split(/\r?\n/)) {
    const url = line.trim()
    if (!url || url.startsWith('#') || seen.has(url)) continue
    seen.add(url)
    out.push(url)
  }
  return out
}

export async function startBatchImport() {
  const urls = parseBatchUrls(batch.draft.urls)
  if (!urls.length) {
    batch.formError = '请粘贴至少一个栏目地址（每行一个）'
    return
  }
  if (urls.length > 20) {
    batch.formError = `一次最多 20 个地址（当前 ${urls.length} 个）`
    return
  }
  const bad = urls.filter((u) => !/^https?:\/\//i.test(u))
  if (bad.length) {
    batch.formError = `有 ${bad.length} 行不是 http(s) 地址：${bad[0]}`
    return
  }
  batch.starting = true
  batch.formError = null
  batch.streamError = null
  try {
    const payload: SourceBatchImportPayload = {
      urls,
      target: batch.draft.target.trim() || null,
      keywords: parseKeywords(batch.draft.keywords),
      limit: Number(batch.draft.limit) || 3,
      pages: Number(batch.draft.pages) || 1,
    }
    const created = await api.startBatchImport(payload)
    batch.batchId = created.batch_id
    batch.snapshot = null
    batch.lastLogs = {}
    // 新一轮队列开始：上一轮的行内善后提示清掉（新作业 id 不同，本不会误伤，整装重来不留旧痕迹）
    for (const key of Object.keys(handledJobs)) delete handledJobs[key]
    subscribeBatch(created.batch_id)
    const rejected = created.rejected.length
    notifySuccess(
      rejected
        ? `批量导入已排队：${created.accepted.length} 个入队，${rejected} 个被拒绝（见列表）`
        : `批量导入已排队：${created.accepted.length} 个地址`,
    )
  } catch (error) {
    batch.formError = text(error)
  } finally {
    batch.starting = false
  }
}

function subscribeBatch(batchId: string) {
  stopBatchStream()
  batchStopStream = subscribeBatchStream(batchId, {
    onPhase: (snapshot) => {
      batch.snapshot = snapshot
    },
    onLog: (jobId, log) => {
      batch.lastLogs = { ...batch.lastLogs, [jobId]: log }
    },
    onDone: (snapshot) => {
      batch.snapshot = snapshot
      batchStopStream = null
      // 成功的草案已落库：来源列表要跟着更新
      void loadSources()
    },
    onError: (message) => {
      batch.streamError = message
      batchStopStream = null
    },
  })
}

export function stopBatchStream() {
  batchStopStream?.()
  batchStopStream = null
}

export async function cancelBatchJob(jobId: string) {
  try {
    await api.cancelSourceImport(jobId)
    notifySuccess('已请求取消该作业')
  } catch (error) {
    notify(`取消失败：${text(error)}`)
  }
}

/** 展开一个子作业看详情：拉整份快照（报告/候选/日志），running 的顺带重订阅单作业流。 */
export async function expandBatchJob(jobId: string | null) {
  stopExpandedStream()
  batch.expandedJobId = jobId
  batch.expandedJob = null
  batch.expandedLogs = []
  if (jobId == null) return
  try {
    const snapshot = await api.sourceImportJob(jobId)
    batch.expandedJob = snapshot
    if (snapshot.status === 'running' || snapshot.status === 'queued') {
      expandedStopStream = subscribeImportStream(jobId, {
        onPhase: (next) => {
          batch.expandedJob = next
        },
        onLog: (line) => {
          batch.expandedLogs = [...batch.expandedLogs, line]
        },
        onDone: (final) => {
          batch.expandedJob = final
          expandedStopStream = null
          void loadSources()
        },
        onError: () => {
          expandedStopStream = null
        },
      })
    }
  } catch {
    batch.expandedJob = null
  }
}

let expandedStopStream: (() => void) | null = null

function stopExpandedStream() {
  expandedStopStream?.()
  expandedStopStream = null
}

export function resetBatch() {
  stopBatchStream()
  stopExpandedStream()
  batch.batchId = null
  batch.snapshot = null
  batch.streamError = null
  batch.expandedJobId = null
  batch.expandedJob = null
  batch.expandedLogs = []
  batch.lastLogs = {}
}

// ---------- 路由离开再回来时的自愈对表 ----------

/**
 * SourcesPage 挂载时调用：组件被路由卸载过（SSE 已断），但状态还在。
 * 在跑的作业重订阅流；已完成/取消的作业补最终快照进报告态。
 */
export async function syncImportsOnMount(): Promise<void> {
  void refreshRecentRuns()
  // —— 单条向导 ——
  if (wizard.step === 'running' && wizard.job && !wizardStopStream) {
    const jobId = wizard.job.job_id
    try {
      const snapshot = await api.sourceImportJob(jobId)
      wizard.job = snapshot
      if (TERMINAL.includes(snapshot.status)) {
        wizard.step = 'report'
        wizard.selectedCandidateIndex =
          snapshot.candidates.find((c) => c.selected)?.index ?? null
        void loadSources()
      } else {
        subscribeWizard(jobId)
      }
    } catch {
      /* 作业已被服务端清理：停在原状，用户可重新接入 */
    }
  }
  // —— 批量队列 ——
  if (batch.batchId && batch.snapshot && !batch.snapshot.all_terminal && !batchStopStream) {
    subscribeBatch(batch.batchId)
  }
}
