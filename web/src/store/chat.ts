/**
 * 对话 Agent 状态（Phase 19）。
 *
 * 放在模块级（而不是 AgentPage 里），与 `store/sourceImports.ts` 同一条理由：
 * 路由离开时页面组件会卸载，但正在生成的回答不该跟着断。本 store **自己持有**流句柄，
 * 页面组件不订阅、也不需要在卸载时做任何事——切走再切回来，消息照样在涨。
 *
 * 会话 = 一个 `thread_id`：后端 ReAct 图挂在 `InMemorySaver` 上按它分桶存上下文，
 * 所以「新对话」= 换一个 id（没有删除会话的接口）。不入库、不落 localStorage，
 * 刷新页面就是新会话。
 */

import { computed, ref } from 'vue'

import { ApiError, api, streamChat } from '../api/client'

export type ChatRole = 'user' | 'assistant'
/** streaming=在飞 / done=正常收尾 / stopped=用户点了停止（半截内容保留） / error=流内或网络错误 */
export type ChatMessageStatus = 'streaming' | 'done' | 'stopped' | 'error'

/** 工具/中间文本步骤（`tool` SSE 事件）：start 时追加，done 时回填 ok；text=true 是被
 * 新一轮顶掉的中间文本段（无工具往返，直接记为 done，见 sendMessage 的 segReset 逻辑）。 */
export interface ChatStep {
  name: string
  detail: string
  status: 'start' | 'done'
  ok: boolean
  text?: boolean
}

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  status: ChatMessageStatus
  error?: string
  /** 本轮工具调用与被顶掉的中间文本（`tool` 事件驱动；没有工具调用时为空） */
  steps?: ChatStep[]
  /**
   * 段标记：出现过 `tool` 事件后置 true，下一个 token 到来时**清空重计**而不是追加。
   * ReAct 每轮工具调用之间会各输出一段中间文本，全部追加会把「第 1 段 + 第 2 段」
   * 连成一片；逐段替换则始终只展示最新一段，直到进入最终回答。
   */
  segReset?: boolean
}

function randomId(prefix: string): string {
  // randomUUID 需要安全上下文（https / localhost）；file:// 打开 dist 时退化到随机串
  return crypto.randomUUID?.() ?? `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export const messages = ref<ChatMessage[]>([])
/** 有在飞的流：驱动「发送 ↔ 停止」按钮态，同时充当防重入闸 */
export const sending = ref(false)

/** 无头浏览器：可用（后端装了 playwright 且 BROWSER_ENABLED=true）≠ 本次已开启。 */
export const browserAvailable = ref(false)
/** 「浏览网页」开关：只对**下一次发送**生效；发送中不可切换（同一请求的 run 已建好）。 */
export const browserEnabled = ref(false)

/** 拉功能开关；后端不可用时保持隐藏，不弹错（对话页照常可用）。 */
export async function loadFeatures(): Promise<void> {
  try {
    const flags = await api.features()
    browserAvailable.value = flags.browser_available
    if (!browserAvailable.value) browserEnabled.value = false
  } catch {
    /* 静默：开关默认隐藏 */
  }
}

/** 切换「浏览网页」；生成中不允许改（界面也会 disable）。 */
export function toggleBrowser(): void {
  if (sending.value || !browserAvailable.value) return
  browserEnabled.value = !browserEnabled.value
}

export const isEmpty = computed(() => messages.value.length === 0)

/** 当前会话 id：按它隔离后端记忆（见文件头注释） */
let threadId = randomId('t')
let activeController: AbortController | null = null

/** 只动最后一条助手消息：它是一个响应式代理，原地改即可（不要整表复制） */
function settleLast(status: ChatMessageStatus, error?: string): void {
  const last = messages.value.at(-1)
  if (!last || last.role !== 'assistant' || last.status !== 'streaming') return
  Object.assign(last, { status, error })
}

/**
 * 发一条消息：立即追加「用户消息 + 空助手消息」，然后开流。
 *
 * 工具/模型都不认识 `thread_id`——它只给 checkpointer 分桶用。
 */
export function sendMessage(raw: string): void {
  const text = raw.trim()
  if (!text || sending.value) return // 空输入 / 上一条还在生成
  messages.value.push(
    { id: randomId('m'), role: 'user', content: text, status: 'done' },
    { id: randomId('m'), role: 'assistant', content: '', status: 'streaming' },
  )
  sending.value = true

  const controller = new AbortController()
  activeController = controller
  void (async () => {
    try {
      await streamChat(
        {
          message: text,
          thread_id: threadId,
          browser_enabled: browserEnabled.value,
        },
        {
          onEvent: (event) => {
            switch (event.type) {
              case 'token': {
                const last = messages.value.at(-1)
                if (last?.role === 'assistant') {
                  if (last.segReset) {
                    // 新一轮（工具调用之后）的第一段：气泡里清掉上一段中间文本，只展示
                    // 最新一段；被顶掉的那段收进「调用过程」列表，不丢信息
                    if (last.content.trim()) {
                      const steps = (last.steps ??= [])
                      steps.push({
                        name: 'say',
                        detail: last.content,
                        status: 'done',
                        ok: true,
                        text: true,
                      })
                    }
                    last.content = event.content
                    last.segReset = false
                  } else {
                    last.content += event.content
                  }
                }
                break
              }
              case 'tool': {
                const last = messages.value.at(-1)
                // 标记下一段 token 要重开：模型这一轮说了什么会在工具结果回来后作废
                if (last?.role === 'assistant') last.segReset = true
                recordToolStep(event)
                break
              }
              case 'error':
                settleLast('error', event.message)
                break
              case 'done':
                settleLast('done')
                break
              // 未知事件类型忽略：以后新增的增量事件不应把消息误判成「已完成」
            }
          },
        },
        controller.signal,
      )
    } catch (error) {
      // 走到这里只可能是网络层失败 / 非 2xx（流内错误是 error 事件，见上）
      settleLast('error', error instanceof ApiError ? error.message : String(error))
    } finally {
      // 只有「还是当前这条流」才收尾：被 stopGeneration / resetChat 中断时它已经接管过了
      if (activeController === controller) {
        activeController = null
        sending.value = false
        settleLast('done') // 服务端没发 done 就断流时的兜底
      }
    }
  })()
}

/**
 * `tool` 事件 → 最后一条助手消息的调用步骤。
 *
 * `start` 追加一条；`done` 回填**最近一条同名且仍在 start 的**步骤（并发的同名动作也
 * 能依次配对），配不上就补一条 done（服务端在客户端连上之前的极短窗口内发完时）。
 */
function recordToolStep(event: {
  name: string
  detail: string
  status: 'start' | 'done'
  ok: boolean
}): void {
  const last = messages.value.at(-1)
  if (!last || last.role !== 'assistant') return
  const steps = (last.steps ??= [])
  if (event.status === 'start') {
    steps.push({
      name: event.name,
      detail: event.detail,
      status: 'start',
      ok: false,
    })
    return
  }
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i]
    if (step.name === event.name && step.status === 'start') {
      Object.assign(step, {
        status: 'done',
        ok: event.ok,
        detail: event.detail || step.detail,
      })
      return
    }
  }
  steps.push({
    name: event.name,
    detail: event.detail,
    status: 'done',
    ok: event.ok,
  })
}

/** 停止当前生成：断开连接，半截内容保留并标注「已停止」。 */
export function stopGeneration(): void {
  if (!sending.value) return
  activeController?.abort()
  activeController = null
  sending.value = false
  settleLast('stopped')
}

/**
 * 重发上一条用户消息（失败 / 停止后的「重试」）。
 *
 * 从那条提问起**整段替换**（含没答完的助手气泡）再重发：只删助手气泡的话，
 * 界面上会出现两条一模一样的提问。
 */
export function retryLast(): void {
  if (sending.value) return
  // 手写倒序查找：`findLastIndex` 是 ES2023，本工程的 lib 还没到（见 tsconfig.app.json）
  let index = -1
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    if (messages.value[i].role === 'user') {
      index = i
      break
    }
  }
  if (index === -1) return
  const text = messages.value[index].content
  messages.value.splice(index)
  sendMessage(text)
}

/**
 * 新对话：中断在飞的流、清空消息、换一个 `thread_id`。
 *
 * **停止后不换 id**（半截回合会留在后端那条线程上）；只有用户主动开新会话才换——
 * 换 id 等于丢掉整段上下文，代价比一条悬空回合大得多。
 */
export function resetChat(): void {
  stopGeneration()
  messages.value = []
  threadId = randomId('t')
}
