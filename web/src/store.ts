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
  FacetList,
  LibraryMeta,
  LibraryPolicyDetail,
  LibraryPolicyItem,
  LogLine,
  PipelineConfig,
  PolicyExtractionResponse,
  PolicyResultItem,
  PolicySummary,
  ScheduleCreatePayload,
  ScheduleJobDTO,
  TaskCreatePayload,
  TaskDTO,
  TaskStatus,
} from './api/types'

/** 页面级阶段：驱动骨架屏 / 空状态 / 错误态 / 检索结果确认面板。 */
export type Phase = 'loading' | 'idle' | 'running' | 'confirming' | 'done' | 'error'

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
/** 「终止抓取」请求进行中 */
export const stopping = ref(false)
/** 「清空」请求进行中 */
export const clearing = ref(false)
/** 「确认抓取」请求进行中（检索结果确认面板） */
export const confirming = ref(false)
/** 结果表格刷新中（不阻塞整页） */
export const refreshing = ref(false)

export const toast = ref<{ message: string; retry?: () => void } | null>(null)

/** 抓取配置表单（ConfigPanel 双向绑定，TopBar 提交） */
export const form = reactive<TaskCreatePayload>({
  source: '国务院政策文件库（gov.cn）',
  date_from: null,
  date_to: null,
  // 不预填关键词：留空时按下方筛选条件浏览（如只看某机构的近期发文），关键词只是可选范围
  keywords: [],
  agencies: [],
  doc_types: [],
  // 高级设置（流水线参数）：先置 null，bootstrap 拉到 .env 默认值后回填，用户可改，随任务提交
  pipeline_max_policies: null,
  pipeline_per_keyword_limit: null,
  pipeline_extract_concurrency: null,
  pipeline_max_body_chars: null,
  pipeline_mcp_timeout: null,
})

/** 筛选器可选值（发布机构 / 文种）；loadFacets 填充，失败时后端已回退静态名单 */
export const facets = ref<FacetList>({ agencies: [], doc_types: [], source: 'fallback' })
export const facetsLoading = ref(false)

/** 生效的流水线参数（高级设置面板只读展示）；加载失败时保持 null，面板显示占位。 */
export const pipelineConfig = ref<PipelineConfig | null>(null)

// ---------- 定时入库（Phase 11） ----------

/** 入库方式：一次性 = 点「开始抓取」立刻跑；定时 = 弹窗配置调度参数，由调度器自动触发 */
export type IngestMode = 'once' | 'schedule'
export const ingestMode = ref<IngestMode>('once')

/** 定时任务弹窗：开关 + 当前视图（列表 / 新建） */
export const scheduleModalOpen = ref(false)
export type ScheduleModalView = 'list' | 'create'
export const scheduleModalView = ref<ScheduleModalView>('list')

export const schedules = ref<ScheduleJobDTO[]>([])
export const schedulesLoading = ref(false)

/** 启用的定时任务数（TopBar 入口旁的小徽标） */
export const enabledScheduleCount = computed(
  () => schedules.value.filter((job) => job.enabled).length,
)

export const phase = computed<Phase>(() => {
  if (booting.value) return 'loading'
  if (!task.value) return 'idle'
  if (task.value.status === 'failed') return 'error'
  if (task.value.status === 'awaiting_confirmation') return 'confirming'
  if (task.value.status === 'running') return 'running'
  return 'done'
})

export const hasResult = computed(() => policies.value.length > 0)

/** 是否可以终止：任务在排队中、运行中，或停在「等确认」（此时终止即放弃本次抓取） */
export const canCancel = computed(() => {
  const status = task.value?.status
  return status === 'pending' || status === 'running' || status === 'awaiting_confirmation'
})

// ---------- 内部 ----------

let stopStream: (() => void) | null = null
let toastTimer: ReturnType<typeof setTimeout> | null = null
/**
 * 用户主动终止 / 清空时为 true：抑制 SSE `done` 事件弹出的「抓取失败」Toast
 * （那两个动作自己会给出提示，否则会重复弹一条误导性文案）。
 */
let manualStop = false

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

/** 首屏：拉当前任务 + 日志 + 结果 + 筛选器名单；没有任务则进入空状态。 */
export async function bootstrap(): Promise<void> {
  booting.value = true
  try {
    // 筛选器名单与任务无关，并行拉（失败时后端已回退静态名单，不阻塞首屏）
    void loadFacets()
    void loadPipelineConfig()
    // 「已入库 N 篇」与抓取任务无关，一并预热
    void loadLibraryMeta()
    // 定时任务列表（TopBar 徽标用）；失败静默，弹窗打开时会再拉一次
    void loadSchedules()
    const current = await api.currentTask()
    task.value = current
    if (current) {
      logs.value = await api.taskLogs(current.id)
      await refreshPolicies(current.id)
      // 停在「等确认」时也要订阅：确认后进度要能继续推过来（服务端在非终态不会关流）
      if (current.status === 'running' || current.status === 'awaiting_confirmation') {
        subscribe(current.id)
      }
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

/** 拉筛选器可选值（发布机构 / 文种）；失败时后端已回退静态名单，这里不弹错。 */
export async function loadFacets(): Promise<void> {
  facetsLoading.value = true
  try {
    facets.value = await api.loadFacets({
      q: form.keywords[0] || undefined,
      date_from: form.date_from,
      date_to: form.date_to,
    })
  } catch {
    /* 后端失败时已回退静态名单；保留旧值即可 */
  } finally {
    facetsLoading.value = false
  }
}

/** 拉生效的流水线参数（.env 默认值）；拉到后回填表单里仍为空的项，用户可在此基础上改。 */
export async function loadPipelineConfig(): Promise<void> {
  try {
    const config = await api.pipelineConfig()
    pipelineConfig.value = config
    for (const key of [
      'pipeline_max_policies',
      'pipeline_per_keyword_limit',
      'pipeline_extract_concurrency',
      'pipeline_max_body_chars',
      'pipeline_mcp_timeout',
    ] as const) {
      if (form[key] == null) form[key] = config[key]
    }
  } catch {
    /* 保持 null，高级设置面板显示占位 */
  }
}

/** 「高级设置」里的恢复默认：把五项重置回 .env 当前值。 */
export function resetPipelineOverrides(): void {
  const config = pipelineConfig.value
  if (!config) return
  form.pipeline_max_policies = config.pipeline_max_policies
  form.pipeline_per_keyword_limit = config.pipeline_per_keyword_limit
  form.pipeline_extract_concurrency = config.pipeline_extract_concurrency
  form.pipeline_max_body_chars = config.pipeline_max_body_chars
  form.pipeline_mcp_timeout = config.pipeline_mcp_timeout
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
      // 手动终止 / 清空自己会给提示，这里不再弹「抓取失败」
      if (manualStop) return
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
    // 日期 / 数字输入框清空后拿到的是空串，后端要的是 null（= 用服务端默认值）
    const { task_id } = await api.createTask({
      ...form,
      date_from: form.date_from || null,
      date_to: form.date_to || null,
      keywords: [...form.keywords],
      pipeline_max_policies: form.pipeline_max_policies || null,
      pipeline_per_keyword_limit: form.pipeline_per_keyword_limit || null,
      pipeline_extract_concurrency: form.pipeline_extract_concurrency || null,
      pipeline_max_body_chars: form.pipeline_max_body_chars || null,
      pipeline_mcp_timeout: form.pipeline_mcp_timeout || null,
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

/** 「检索结果确认」：提交勾选的篇目 → 后端丢弃未勾选的并开始提取。 */
export async function confirmSelection(policyIds: number[]): Promise<void> {
  const current = task.value
  if (!current || confirming.value) return
  if (!policyIds.length) {
    showToast('请至少勾选一篇要抓取的政策')
    return
  }
  confirming.value = true
  try {
    task.value = await api.confirmTask(current.id, policyIds)
    await refreshPolicies(current.id)
    // 首屏若停在「等确认」已经订阅过；这里兜底，保证后续进度有流可推
    if (!stopStream) subscribe(current.id)
  } catch (error) {
    showToast(`确认失败：${errorText(error)}`)
  } finally {
    confirming.value = false
  }
}

/** 点「终止抓取」：取消后台流水线 → 任务落「失败 · 任务已手动终止」终态，可重新开始。 */
export async function cancelTask(): Promise<void> {
  const current = task.value
  if (!current || stopping.value || !canCancel.value) return
  stopping.value = true
  manualStop = true
  try {
    task.value = await api.cancelTask(current.id)
    stopSubscription()
    await Promise.all([refreshPolicies(current.id), refreshLogs()])
    showToast('已终止本次抓取')
  } catch (error) {
    // 失败时保持订阅不变（如 409「任务已结束」），由 SSE 继续同步真实状态
    showToast(`终止失败：${errorText(error)}`)
  } finally {
    stopping.value = false
    manualStop = false
  }
}

/** 点「清空」：删除全部任务与结果，页面回到「尚未抓取」空状态。 */
export async function clearAll(): Promise<void> {
  if (clearing.value) return
  clearing.value = true
  manualStop = true
  // 任务即将被删除，先断开 SSE：否则服务端下一轮会推「任务不存在」错误事件
  stopSubscription()
  try {
    const { deleted } = await api.clearTasks()
    task.value = null
    logs.value = []
    policies.value = []
    summary.value = { ...EMPTY_SUMMARY }
    showToast(deleted ? `已清空 ${deleted} 个抓取任务` : '暂无可清空的数据')
  } catch (error) {
    showToast(`清空失败：${errorText(error)}`)
    await bootstrap() // 清空失败：把服务端的真实状态拉回来
  } finally {
    clearing.value = false
    manualStop = false
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

// ---------- 定时入库动作（Phase 11） ----------

export function openScheduleModal(view: ScheduleModalView): void {
  scheduleModalView.value = view
  scheduleModalOpen.value = true
  void loadSchedules()
}

export function closeScheduleModal(): void {
  scheduleModalOpen.value = false
}

/** 拉定时任务列表（弹窗打开时调用，保证看到最新排程）。 */
export async function loadSchedules(): Promise<void> {
  schedulesLoading.value = true
  try {
    schedules.value = await api.listSchedules()
  } catch (error) {
    showToast(`定时任务列表加载失败：${errorText(error)}`)
  } finally {
    schedulesLoading.value = false
  }
}

/** 把当前抓取配置表单快照成定时任务提交体（创建后改表单不影响已建任务）。 */
export function buildSchedulePayload(): Omit<ScheduleCreatePayload, 'schedule_kind' | 'every' | 'run_time' | 'run_weekday'> {
  return {
    source: form.source,
    date_from: form.date_from || null,
    keywords: [...form.keywords],
    agencies: [...form.agencies],
    doc_types: [...form.doc_types],
    pipeline_max_policies: form.pipeline_max_policies || null,
    pipeline_per_keyword_limit: form.pipeline_per_keyword_limit || null,
    pipeline_extract_concurrency: form.pipeline_extract_concurrency || null,
    pipeline_max_body_chars: form.pipeline_max_body_chars || null,
    pipeline_mcp_timeout: form.pipeline_mcp_timeout || null,
  }
}

export async function createSchedule(payload: ScheduleCreatePayload): Promise<boolean> {
  try {
    const job = await api.createSchedule(payload)
    showToast(`已创建定时任务「${job.name}」，下次触发：${formatRunAt(job.next_run_at)}`)
    await loadSchedules()
    return true
  } catch (error) {
    showToast(`创建定时任务失败：${errorText(error)}`)
    return false
  }
}

export async function toggleScheduleJob(job: ScheduleJobDTO): Promise<void> {
  try {
    const { job: next } = await api.toggleSchedule(job.id)
    schedules.value = schedules.value.map((item) => (item.id === next.id ? next : item))
    showToast(next.enabled ? `「${next.name}」已启用` : `「${next.name}」已停用`)
  } catch (error) {
    showToast(`切换失败：${errorText(error)}`)
  }
}

export async function deleteScheduleJob(job: ScheduleJobDTO): Promise<void> {
  try {
    await api.deleteSchedule(job.id)
    schedules.value = schedules.value.filter((item) => item.id !== job.id)
    showToast(`已删除定时任务「${job.name}」`)
  } catch (error) {
    showToast(`删除失败：${errorText(error)}`)
  }
}

/** 立即运行一轮：进度不在弹窗里展示，刷新当前任务视图到抓取进度面板看。 */
export async function runScheduleNow(job: ScheduleJobDTO): Promise<void> {
  try {
    await api.runSchedule(job.id)
    showToast(`「${job.name}」已触发，进度见抓取进度面板`)
    void bootstrap()
  } catch (error) {
    showToast(`触发失败：${errorText(error)}`)
  }
}

/** next_run_at / last_run_at 的短格式展示（MM-DD HH:mm，本地时区）。 */
export function formatRunAt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export async function refreshLogs(): Promise<void> {
  if (task.value) logs.value = await api.taskLogs(task.value.id)
}

export function isRunning(status: TaskStatus | undefined): boolean {
  return status === 'running'
}

// ---------- 成果库（跨任务沉淀；页面切换不走路由，用 view 状态驱动） ----------

export type AppView = 'crawl' | 'library' | 'library-detail'

export const view = ref<AppView>('crawl')
export const libraryPolicyId = ref<number | null>(null)

export const libraryItems = ref<LibraryPolicyItem[]>([])
export const libraryTotal = ref(0)
export const libraryPage = ref(1)
export const libraryPageSize = ref(6)
export const libraryLoading = ref(false)
export const libraryMeta = ref<LibraryMeta | null>(null)

/** 筛选条件：空串 = 不过滤；改动后自动回到第一页 */
export const libraryFilters = reactive({
  keyword: '',
  agency: '',
  doc_type: '',
  year: '',
})

export const libraryDetail = ref<LibraryPolicyDetail | null>(null)
export const libraryDetailLoading = ref(false)
export const libraryDetailError = ref(false)
export const libraryReextracting = ref(false)

let detailPollTimer: ReturnType<typeof setTimeout> | null = null

function stopDetailPoll(): void {
  if (detailPollTimer) clearTimeout(detailPollTimer)
  detailPollTimer = null
}

/** 拉成果库头部统计 + 筛选选项（TopBar「已入库 N 篇」与库页头部共用）。 */
export async function loadLibraryMeta(): Promise<void> {
  try {
    libraryMeta.value = await api.libraryMeta()
  } catch {
    /* 保持旧值；顶栏数字不至于因为一次失败消失 */
  }
}

/** 拉成果库列表的一页；页码越界时（过滤后总页数变少）自动回收。 */
export async function loadLibraryPage(page = libraryPage.value): Promise<void> {
  libraryLoading.value = true
  try {
    const data = await api.libraryPolicies({
      page,
      page_size: libraryPageSize.value,
      keyword: libraryFilters.keyword.trim() || undefined,
      agency: libraryFilters.agency || undefined,
      doc_type: libraryFilters.doc_type || undefined,
      year: libraryFilters.year ? Number(libraryFilters.year) : undefined,
    })
    libraryItems.value = data.items
    libraryTotal.value = data.total
    libraryPage.value = data.page
    if (!data.items.length && data.total > 0 && data.page > 1) {
      await loadLibraryPage(data.page - 1)
    }
  } catch (error) {
    showToast(`成果库加载失败：${errorText(error)}`)
  } finally {
    libraryLoading.value = false
  }
}

/** 改筛选条件：合并 → 回第一页 → 重拉。 */
export function setLibraryFilter(patch: Partial<typeof libraryFilters>): void {
  Object.assign(libraryFilters, patch)
  void loadLibraryPage(1)
}

/** 切到成果库页：刷新统计 + 第一页列表。 */
export function goLibrary(): void {
  view.value = 'library'
  void loadLibraryMeta()
  void loadLibraryPage(1)
}

/** 切回抓取任务页。 */
export function goCrawl(): void {
  view.value = 'crawl'
}

/** 成果库详情页数据。 */
export async function loadLibraryDetail(policyId: number): Promise<void> {
  stopDetailPoll()
  libraryDetailLoading.value = true
  libraryDetailError.value = false
  libraryDetail.value = null
  try {
    libraryDetail.value = await api.libraryPolicy(policyId)
    // 详情页打开时恰好遇到「提取中」（如重新提取后刷新页面）：轮询到终态
    if (libraryDetail.value.extraction.status === 'extracting') {
      scheduleDetailPoll(policyId)
    }
  } catch (error) {
    libraryDetailError.value = true
    showToast(`政策详情加载失败：${errorText(error)}`)
  } finally {
    libraryDetailLoading.value = false
  }
}

function scheduleDetailPoll(policyId: number, attempts = 0): void {
  if (attempts >= 60) return // 最长约 3 分钟，之后停在「提取中」由用户手动刷新
  stopDetailPoll()
  detailPollTimer = setTimeout(() => {
    void (async () => {
      try {
        const data = await api.libraryPolicy(policyId)
        if (view.value === 'library-detail' && libraryPolicyId.value === policyId) {
          libraryDetail.value = data
        }
        if (data.extraction.status === 'extracting') {
          scheduleDetailPoll(policyId, attempts + 1)
        } else {
          void loadLibraryMeta()
          void loadLibraryPage()
        }
      } catch {
        scheduleDetailPoll(policyId, attempts + 1)
      }
    })()
  }, 3000)
}

/** 成果库卡片 → 详情页。 */
export function openLibraryPolicy(policyId: number): void {
  libraryPolicyId.value = policyId
  view.value = 'library-detail'
  void loadLibraryDetail(policyId)
}

/** 详情页「返回成果库」：刷新当前页（计数可能已变）后返回。 */
export function backToLibrary(): void {
  view.value = 'library'
  stopDetailPoll()
  void loadLibraryPage()
}

/** 详情页「重新提取」：复用 /reextract，提交后轮询到提取完成。 */
export async function reextractLibraryPolicy(): Promise<void> {
  const id = libraryPolicyId.value
  if (id == null || libraryReextracting.value) return
  libraryReextracting.value = true
  try {
    await api.reextractPolicy(id)
    notify('已提交重新提取，正在后台重新解析该篇')
    if (libraryDetail.value) {
      libraryDetail.value = {
        ...libraryDetail.value,
        extraction: { ...libraryDetail.value.extraction, status: 'extracting' },
      }
    }
    scheduleDetailPoll(id)
  } catch (error) {
    showToast(`重新提取失败：${errorText(error)}`)
  } finally {
    libraryReextracting.value = false
  }
}
