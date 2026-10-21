/**
 * 全局状态（模块级单例 store）。
 *
 * 四个面板是兄弟组件，共享同一份数据：TopBar 提交表单、ProgressPanel 展示进度、
 * ResultTable 展示结果、DetailDrawer 展示单篇详情——都由这里驱动。
 * 没有引入 Pinia：状态量小，一个模块 + `reactive/ref` 足够，也少一个依赖。
 *
 * 页面切换自 Phase 16 起由 vue-router 负责（本文件不再持有 view 状态）；
 * 这里保留的 `router` 引用只用于在**动作**里读当前路由（clearAll 的分支、
 * 详情轮询的守卫），模块级 store 不能用 useRoute() 组合式 API。
 */

import { computed, reactive, ref } from 'vue'

import { ApiError, api, subscribeTaskStream } from './api/client'
import { router } from './router'
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
  SourceDTO,
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

/** 提示类型：决定图标与配色（成功=绿勾 / 失败=红叉 / 信息=蓝i） */
export type ToastKind = 'success' | 'error' | 'info'

export const toast = ref<{
  message: string
  kind: ToastKind
  /** 提示附带的动作按钮（不传则该条 Toast 只有文本） */
  retry?: () => void
  /** 动作按钮的文案；默认「重试」（多数场景就是重试失败的那一步） */
  actionLabel?: string
} | null>(null)

/** 抓取配置表单（ConfigPanel 双向绑定，TopBar 提交） */
export const form = reactive<TaskCreatePayload>({
  source: '国务院政策文件库（gov.cn）',
  date_from: null,
  date_to: null,
  // 不预填关键词：留空时按下方筛选条件浏览（如只看某机构的近期发文），关键词只是可选范围
  keywords: [],
  agencies: [],
  doc_types: [],
  // 无头浏览器来源的浏览目标（仅 engine=browser_driven 时提交；入口网址以来源配置为准）
  browse_url: null,
  browse_goal: null,
  // 高级设置（流水线参数）：先置 null，bootstrap 拉到 .env 默认值后回填，用户可改，随任务提交
  pipeline_max_policies: null,
  pipeline_per_keyword_limit: null,
  pipeline_extract_concurrency: null,
  pipeline_max_body_chars: null,
  pipeline_mcp_timeout: null,
  // 提取模型（.env PIPELINE_MODELS 注册表 id）；同上回填为默认模型后随任务提交
  pipeline_model: null,
})

/** 筛选器可选值（发布机构 / 文种）；loadFacets 填充，失败时后端已回退静态名单 */
export const facets = ref<FacetList>({
  agencies: [],
  doc_types: [],
  source: 'fallback',
  // 初始按「支持全部筛选项」渲染，拉到真实名单后以后端为准
  supports: {
    agencies: true,
    doc_types: true,
    keyword_server_side: true,
    date_server_side: true,
    note: null,
  },
  source_label: null,
})
export const facetsLoading = ref(false)

/** 生效的流水线参数（高级设置面板只读展示）；加载失败时保持 null，面板显示占位。 */
export const pipelineConfig = ref<PipelineConfig | null>(null)

// ---------- 抓取来源（Phase 15） ----------

/** 来源清单：内置 gov.cn + `content_source` 的所有行（含草案/已停用，供管理列表展示）。 */
export const sources = ref<SourceDTO[]>([])
export const sourcesLoading = ref(false)
/** 内置来源的 label（后端给）：前端校验当前选中项、回退时用它。 */
export const defaultSourceLabel = ref('国务院政策文件库（gov.cn）')

/** 下拉里可选的来源：内置的 + 已启用的。草案/已停用不进下拉（选了也跑不动）。 */
export const selectableSources = computed(() =>
  sources.value.filter((item) => item.builtin || item.status === 'active'),
)

/** 当前选中的来源对象（下拉里找不到时为 undefined，表单会回退到内置来源）。 */
export const currentSource = computed(() =>
  sources.value.find((item) => item.label === form.source),
)

/** 当前是否选中了无头浏览器来源（engine=browser_driven，来源管理里「保存为无头浏览器来源」创建）。

 * 入口网址存在来源配置里（服务端取用），表单只管浏览目标；机构/文种筛选隐藏。 */
export const isBrowserSource = computed(() => currentSource.value?.engine === 'browser_driven')

/** 当前来源是否支持文种筛选（配置化来源 / 浏览器来源没有文种，选中时前端要隐藏该筛选项）。 */
export const sourceSupportsDocTypes = computed(
  () =>
    !isBrowserSource.value &&
    facets.value.supports.doc_types &&
    currentSource.value?.supports_doc_types !== false,
)

/**
 * 当前来源的**筛选口径提示**（配置化来源才需要），null = 无需提示（内置 gov.cn 由接口执行）。
 *
 * 为什么非要说出来：栏目列表页给不了检索能力，关键词只能逐条比对已翻到的标题、
 * 时间范围只能逐条比对列表页上那个日期，**召回上限 = 翻页深度 × 每页条数**。
 * 不说的话用户会把「筛过了」当成「筛全了」——那是这个仓里最贵的一类误解。
 * 口径的来源在后端（`sources/config.capability_summary`），这里只负责翻译成人话。
 */
export const sourceFilterNote = computed(() => {
  const source = currentSource.value
  if (!source || source.builtin) return null
  const bits: string[] = []
  if (source.keyword_server_side) {
    const names = source.search_entrypoints.join('、')
    bits.push(
      `关键词送进该站点的站内检索入口（${names}），召回由站点索引决定；` +
        '一旦选了发布机构，就只在该栏目内做标题匹配',
    )
  } else {
    bits.push('该来源没有站内检索入口：关键词只在已翻到的列表页里比对标题')
  }
  bits.push(
    source.date_server_side
      ? '时间范围由站点过滤'
      : '时间范围在取回后复核，翻页范围内的条目才能被筛到',
  )
  return `「${source.label}」的筛选口径：${bits.join('；')}。翻页深度决定召回上限。`
})

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

function showToast(
  message: string,
  kind: ToastKind = 'error',
  retry?: () => void,
  actionLabel = '重试',
): void {
  toast.value = { message, kind, retry, actionLabel }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 8000)
}

/**
 * 组件里的一般性提示；`kind` 决定图标与配色，不传默认 error（红叉）——
 * 不带 kind 的调用点绝大多数是「某某失败」，成功/中性提示请用 notifySuccess / 显式 kind。
 */
export function notify(
  message: string,
  action?: () => void,
  actionLabel?: string,
  kind: ToastKind = 'error',
): void {
  showToast(message, kind, action, actionLabel)
}

/** 成功 / 完成类提示（绿勾），如「已删除」「已提交」。 */
export function notifySuccess(message: string, action?: () => void, actionLabel?: string): void {
  showToast(message, 'success', action, actionLabel)
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

// ---------- 发现「不是这张页面发起」的任务 ----------

/**
 * 已经就「换了任务」提示过的 id：只提示一次，否则每次轮询都会再弹一条
 * （提示是给用户一个选择，不是催他；真要看时点按钮即可，本地任务跑完也会自动对齐）。
 */
let notifiedTaskId: number | null = null
let taskWatchTimer: number | null = null
let taskWatchHandler: (() => void) | null = null

/** 轮询节奏：定时任务到点触发是「秒级到分钟级」的事，15s 足够，成本是一次很小的 GET。 */
const TASK_WATCH_MS = 15000

/**
 * 轮询 `GET /tasks/current`，发现**换了任务**就对齐——任务的产生途径不止这张页面
 * （定时任务到点触发、另一个标签页、另一台设备），store 自己不会知道。
 *
 * 两种情形分开处理：
 * - 本地没有在看的任务、或看的那条已终态 → 直接 `syncTask()` 对齐（用户没损失）。
 * - 本地正在看一条**非终态**任务（跑着 / 等下确认）→ **不抢视图**，只提示「有新的任务」并给一个
 *   按钮：抓取页认的是 `/tasks/current`，一换就再也看不到原来那条（页面还没有任务列表），
 *   所以这个切换必须由用户点。等本地那条跑完，下一次轮询会自己对齐。
 *
 * 只在页面可见时轮询；切回本标签页（`visibilitychange`）立刻查一次——覆盖「在后台放了很久」。
 */
export function startTaskWatch(intervalMs = TASK_WATCH_MS): void {
  stopTaskWatch()
  taskWatchHandler = () => {
    if (document.visibilityState === 'visible') void pollCurrentTask()
  }
  taskWatchTimer = window.setInterval(taskWatchHandler, intervalMs)
  document.addEventListener('visibilitychange', taskWatchHandler)
}

export function stopTaskWatch(): void {
  if (taskWatchTimer != null) window.clearInterval(taskWatchTimer)
  taskWatchTimer = null
  if (taskWatchHandler) document.removeEventListener('visibilitychange', taskWatchHandler)
  taskWatchHandler = null
}

let polling = false

async function pollCurrentTask(): Promise<void> {
  // 定时器与「切回本标签页」可能同时触发，重入会让两次对齐各订阅一次流
  if (polling || booting.value) return
  polling = true
  try {
    let current: TaskDTO | null
    try {
      current = await api.currentTask()
    } catch {
      return // 网络抖动 / 服务重启：下一次轮询再试，不打扰用户
    }
    const nextId = current?.id ?? null
    if (nextId === (task.value?.id ?? null)) return

    if (canCancel.value) {
      // 正在看一条非终态任务：提示即可，别把用户正在看的东西换掉
      if (notifiedTaskId !== nextId) {
        notifiedTaskId = nextId
        if (current) {
          // 不带「定时任务」的断言：DTO 里没有来源标记，定时 / 别的标签页 / 别的机器都可能
          notify(
            `有新的抓取任务 #${current.id}「${current.source}」已开始`,
            () => void syncTask(),
            '查看',
            'info',
          )
        } else {
          notify('当前任务已被删除', () => void syncTask(), '刷新', 'info')
        }
      }
      return
    }
    await syncTask()
  } finally {
    polling = false
  }
}

// ---------- 动作 ----------

/**
 * 把浏览器端的「当前任务」与库里对齐：以 `GET /api/tasks/current`（id 最大的那条）为准，
 * 重拉日志 / 结果，非终态时重订阅进度流；库里没有任务则回到空状态。
 *
 * 为什么它必须是一个**显式动作**：`task` 只是 store 里的一份缓存，而任务的产生途径不止
 * 「抓取页点开始」——来源页的**试运行**、**定时任务**、另一个标签页都会在库里新建任务，
 * store 不会自己知道。不显式对齐的话，抓取页会一直显示「尚未抓取」，而那条任务其实正在跑
 * （或正停在人工确认）。
 */
export async function syncTask(): Promise<void> {
  stopSubscription()
  // 对齐之后重新武装「换任务了」的提示（见 startTaskWatch）
  notifiedTaskId = null
  const current = await api.currentTask()
  task.value = current
  if (!current) {
    logs.value = []
    policies.value = []
    summary.value = { ...EMPTY_SUMMARY }
    return
  }
  logs.value = await api.taskLogs(current.id)
  await refreshPolicies(current.id)
  // **非终态**一律订阅：`pending` 也要（试抓任务落库时就是 pending，要等它转成 running / 等确认；
  // 不订阅的话进度面板会一直停在那一刻）；停在「等确认」时同样要订阅，确认后进度才能继续推过来。
  // 服务端在非终态不会关流，终态则首轮即发 `done` 后关闭，所以多订阅一次是安全的。
  if (
    current.status === 'pending' ||
    current.status === 'running' ||
    current.status === 'awaiting_confirmation'
  ) {
    subscribe(current.id)
  }
}

/** 首屏：拉当前任务 + 日志 + 结果 + 筛选器名单；没有任务则进入空状态。 */
export async function bootstrap(): Promise<void> {
  booting.value = true
  try {
    // 筛选器名单与任务无关，并行拉（失败时后端已回退静态名单，不阻塞首屏）
    void loadFacets()
    void loadPipelineConfig()
    // 抓取来源清单（来源下拉 + /sources 页）；失败静默，来源页挂载时会再拉一次
    void loadSources()
    // 「已入库 N 篇」与抓取任务无关，一并预热
    void loadLibraryMeta()
    // 定时任务列表（TopBar 徽标用）；失败静默，弹窗打开时会再拉一次
    void loadSchedules()
    await syncTask()
  } catch (error) {
    showToast(`初始化失败：${errorText(error)}`)
  } finally {
    booting.value = false
  }
}

/** 拉筛选器可选值（发布机构 / 文种）；失败时后端已回退静态名单，这里不弹错。 */
export async function loadFacets(): Promise<void> {
  if (isBrowserSource.value) {
    // 任意站点没有站内 facet 接口：不发请求，直接把筛选项清空（入口网址由表单接住）。
    facets.value = {
      agencies: [],
      doc_types: [],
      source: 'fallback',
      supports: {
        agencies: false,
        doc_types: false,
        keyword_server_side: false,
        date_server_side: false,
        note: '无头浏览器来源没有站内检索与筛选项：由浏览目标与时间范围在发现循环里筛选',
      },
      source_label: form.source,
    }
    facetsLoading.value = false
    return
  }
  facetsLoading.value = true
  try {
    facets.value = await api.loadFacets({
      q: form.keywords[0] || undefined,
      date_from: form.date_from,
      date_to: form.date_to,
      // 口径随来源变：配置化来源的「发布机构」是栏目名，且没有文种（见 supports）
      source: form.source,
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
    // 提取模型回填默认（注册表第一项）；当前选择已不在注册表里（.env 改过、后端热加载）时也吸回默认
    if (!config.models.some((m) => m.id === form.pipeline_model)) {
      form.pipeline_model = config.default_model
    }
  } catch {
    /* 保持 null，高级设置面板显示占位 */
  }
}

// ---------- 抓取来源动作（Phase 15） ----------

/** 拉来源清单（首屏、进入来源管理页、注册/启用/停用/删除后都调它）。 */
export async function loadSources(): Promise<void> {
  sourcesLoading.value = true
  try {
    const response = await api.listSources()
    sources.value = response.items
    defaultSourceLabel.value = response.default_label
    // 当前选中的来源可能已被删除 / 停用：回退到内置来源。后端对认不出的 label 也会回退到
    // gov.cn，但那是静默的——用户会以为抓的是自己选的站，这里显式回退并重拉名单。
    const selectable = response.items.filter((item) => item.builtin || item.status === 'active')
    if (!selectable.some((item) => item.label === form.source)) {
      form.source = response.default_label
      form.agencies = []
      form.doc_types = []
      void loadFacets()
    }
  } catch {
    /* 拉不到来源就保持现有选中值：后端解析不到 label 时回退内置源，抓取仍可用 */
  } finally {
    sourcesLoading.value = false
  }
}

/** 切换抓取来源：筛选口径随之改变（机构名单不同、可能没有文种），所以清掉旧筛选并重拉名单。 */
export function setSource(label: string): void {
  if (label === form.source) return
  form.source = label
  form.agencies = []
  form.doc_types = []
  // 无头浏览器来源不能定时（浏览目标每期都要人填）：把入库方式掰回一次性
  const picked = sources.value.find((item) => item.label === label)
  if (picked?.engine === 'browser_driven') ingestMode.value = 'once'
  void loadFacets()
}

/** 清空无头浏览器来源的浏览目标（切回普通来源时由表单调用，避免误提交）。 */
export function clearBrowseFields(): void {
  form.browse_url = null
  form.browse_goal = null
}

/** 「高级设置」里的恢复默认：把各项重置回 .env 当前值（模型回默认项）。 */
export function resetPipelineOverrides(): void {
  const config = pipelineConfig.value
  if (!config) return
  form.pipeline_max_policies = config.pipeline_max_policies
  form.pipeline_per_keyword_limit = config.pipeline_per_keyword_limit
  form.pipeline_extract_concurrency = config.pipeline_extract_concurrency
  form.pipeline_max_body_chars = config.pipeline_max_body_chars
  form.pipeline_mcp_timeout = config.pipeline_mcp_timeout
  form.pipeline_model = config.default_model
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
        showToast(`抓取失败：${final.stage ?? '未知原因'}`, 'error', () => void startTask())
      }
    },
    onError: (message) => showToast(message, 'error', () => void startTask()),
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
      // 入口网址以无头来源的服务端配置为准（传了也忽略）；浏览目标只对它有意义
      browse_url: null,
      browse_goal: isBrowserSource.value ? form.browse_goal || null : null,
      pipeline_max_policies: form.pipeline_max_policies || null,
      pipeline_per_keyword_limit: form.pipeline_per_keyword_limit || null,
      pipeline_extract_concurrency: form.pipeline_extract_concurrency || null,
      pipeline_max_body_chars: form.pipeline_max_body_chars || null,
      pipeline_mcp_timeout: form.pipeline_mcp_timeout || null,
      pipeline_model: form.pipeline_model || null,
    })
    task.value = await api.task(task_id)
    policies.value = []
    summary.value = { ...EMPTY_SUMMARY }
    subscribe(task_id)
  } catch (error) {
    showToast(`抓取失败：${errorText(error)}`, 'error', () => void startTask())
  } finally {
    starting.value = false
  }
}

/** 「检索结果确认」：提交勾选的篇目 → 后端丢弃未勾选的并开始提取。 */
export async function confirmSelection(policyIds: number[]): Promise<void> {
  const current = task.value
  if (!current || confirming.value) return
  if (!policyIds.length) {
    showToast('请至少勾选一篇要抓取的政策', 'info')
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
    showToast('已终止本次抓取', 'success')
  } catch (error) {
    // 失败时保持订阅不变（如 409「任务已结束」），由 SSE 继续同步真实状态
    showToast(`终止失败：${errorText(error)}`)
  } finally {
    stopping.value = false
    manualStop = false
  }
}

/** 点「清空」（成果库页顶栏）：删除全部任务与结果，抓取页回到「尚未抓取」，库页同步刷新。 */
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
    showToast(deleted ? `已清空 ${deleted} 个抓取任务` : '暂无可清空的数据', 'success')
    // 按钮在成果库/详情页顶栏：库里的条目随任务级联删除，列表与计数要跟着刷新
    void loadLibraryMeta()
    // 页面切换已由路由负责：详情页随任务级联删除时回列表；列表页原地重拉
    const route = router.currentRoute.value
    if (route.name === 'library-detail') {
      router.replace({ name: 'library' })
    } else if (route.name === 'library') {
      void loadLibraryPage(1)
    }
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
    pipeline_model: form.pipeline_model || null,
  }
}

export async function createSchedule(payload: ScheduleCreatePayload): Promise<boolean> {
  try {
    const job = await api.createSchedule(payload)
    showToast(`已创建定时任务「${job.name}」，下次触发：${formatRunAt(job.next_run_at)}`, 'success')
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
    showToast(next.enabled ? `「${next.name}」已启用` : `「${next.name}」已停用`, 'success')
  } catch (error) {
    showToast(`切换失败：${errorText(error)}`)
  }
}

export async function deleteScheduleJob(job: ScheduleJobDTO): Promise<void> {
  try {
    await api.deleteSchedule(job.id)
    schedules.value = schedules.value.filter((item) => item.id !== job.id)
    showToast(`已删除定时任务「${job.name}」`, 'success')
  } catch (error) {
    showToast(`删除失败：${errorText(error)}`)
  }
}

/** 立即运行一轮：进度不在弹窗里展示，刷新当前任务视图到抓取进度面板看。 */
export async function runScheduleNow(job: ScheduleJobDTO): Promise<void> {
  try {
    await api.runSchedule(job.id)
    showToast(`「${job.name}」已触发，进度见抓取进度面板`, 'success')
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

// ---------- 成果库（跨任务沉淀；自 Phase 16 起页面切换由路由负责，这里只存数据） ----------

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

/** 成果库详情页数据（由路由组件按 route.params.id 调用）。 */
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
        // 守卫跟着路由走：只在还停在该详情页时写状态（route.params 是字符串，要 Number）
        const route = router.currentRoute.value
        if (route.name === 'library-detail' && Number(route.params.id) === policyId) {
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

/** 详情页「重新提取」：复用 /reextract，提交后轮询到提取完成。 */
export async function reextractLibraryPolicy(policyId: number): Promise<void> {
  if (libraryReextracting.value) return
  libraryReextracting.value = true
  try {
    await api.reextractPolicy(policyId)
    notifySuccess('已提交重新提取，正在后台重新解析该篇')
    if (libraryDetail.value) {
      libraryDetail.value = {
        ...libraryDetail.value,
        extraction: { ...libraryDetail.value.extraction, status: 'extracting' },
      }
    }
    scheduleDetailPoll(policyId)
  } catch (error) {
    showToast(`重新提取失败：${errorText(error)}`)
  } finally {
    libraryReextracting.value = false
  }
}
