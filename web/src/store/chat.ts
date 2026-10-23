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

import { ApiError, streamChat } from '../api/client'
import { loadSources } from '../store'
import type { ChatUsage } from '../api/types'

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

/**
 * 最近一轮的用量与耗时（输入框底部那行小字）。
 *
 * ReAct 一轮可能调用模型多次（每次工具往返一次），后端对**每次对话模型调用**发一条
 * `usage` 事件，这里累加；工具内部模型调用的用量不上报（后端已按节点过滤）。
 * 首字 / 总耗时由前端计时，网关不返回 usage 时也能显示这条线。
 */
export interface TurnStats {
  model: string | null
  inputTokens: number
  outputTokens: number
  cachedTokens: number
  reasoningTokens: number
  /** 本轮对话模型调用次数（>1 说明发生了工具往返） */
  modelCalls: number
  /** 本轮工具调用次数（`tool` start 事件数） */
  toolCalls: number
  firstTokenMs: number | null
  elapsedMs: number | null
}

/** 会话累计（「新对话」清零）；只统计对话模型自己的调用 */
export interface SessionUsage {
  inputTokens: number
  outputTokens: number
  cachedTokens: number
}

export const lastTurn = ref<TurnStats | null>(null)
export const sessionUsage = ref<SessionUsage>({
  inputTokens: 0,
  outputTokens: 0,
  cachedTokens: 0,
})

/**
 * 会改来源清单的对话工具：工具跑完且成功时重拉来源列表——否则 agent 在对话里
 * 接入 / 启停 / 删除来源后，抓取表单下拉与来源管理页要整页刷新才能看到变化。
 * `manage_ingestion_job` 的 list/detail 动作也会触发一次额外拉取：多一次无害的
 * GET，比把每个 action 白名单进来要脆。
 */
const SOURCE_MUTATING_TOOLS = new Set(['register_source', 'import_source', 'manage_ingestion_job'])

function blankTurn(): TurnStats {
  return {
    model: null,
    inputTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    reasoningTokens: 0,
    modelCalls: 0,
    toolCalls: 0,
    firstTokenMs: null,
    elapsedMs: null,
  }
}

function recordUsage(event: ChatUsage): void {
  const turn = (lastTurn.value ??= blankTurn())
  turn.model = event.model || turn.model
  turn.inputTokens += event.input_tokens || 0
  turn.outputTokens += event.output_tokens || 0
  turn.cachedTokens += event.cached_tokens || 0
  turn.reasoningTokens += event.reasoning_tokens || 0
  turn.modelCalls += 1
  sessionUsage.value.inputTokens += event.input_tokens || 0
  sessionUsage.value.outputTokens += event.output_tokens || 0
  sessionUsage.value.cachedTokens += event.cached_tokens || 0
}

export const isEmpty = computed(() => messages.value.length === 0)

/** 当前会话 id：按它隔离后端记忆（见文件头注释） */
let threadId = randomId('t')
let activeController: AbortController | null = null
/** 本轮开始时刻（`performance.now()`）；停止路径在 sendMessage 的 finally 之外，也要用它算耗时 */
let turnStartedAt = 0

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
  // 新的一轮：用量从零记（会话累计不清），耗时从前端收到请求开始算
  lastTurn.value = blankTurn()
  turnStartedAt = performance.now()
  const startedAt = turnStartedAt

  const controller = new AbortController()
  activeController = controller
  void (async () => {
    try {
      await streamChat(
        {
          message: text,
          thread_id: threadId,
        },
        {
          onEvent: (event) => {
            switch (event.type) {
              case 'token': {
                // 首字延迟：第一个 token 到达的时间（含排队与工具前的模型调用）
                const turn = lastTurn.value
                if (turn && turn.firstTokenMs === null) {
                  turn.firstTokenMs = Math.round(performance.now() - startedAt)
                }
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
                if (event.status === 'start' && lastTurn.value) {
                  lastTurn.value.toolCalls += 1
                }
                recordToolStep(event)
                // 注册/启停来源的工具刚刚跑完且成功：重拉来源清单（shared store），
                // 让新来源立刻出现在抓取表单下拉与来源管理页，不用整页刷新
                if (
                  event.status === 'done' &&
                  event.ok &&
                  SOURCE_MUTATING_TOOLS.has(event.name)
                ) {
                  void loadSources()
                }
                break
              }
              case 'usage':
                recordUsage(event)
                break
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
        if (lastTurn.value) {
          lastTurn.value.elapsedMs = Math.round(performance.now() - startedAt)
        }
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
  // 停止路径不走 sendMessage 的 finally（它在 activeController 已被清空后跳过），
  // 这里补上耗时，底部小字不至于永远显示「进行中」
  if (lastTurn.value && lastTurn.value.elapsedMs === null) {
    lastTurn.value.elapsedMs = Math.round(performance.now() - turnStartedAt)
  }
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
  // 用量是「这次会话」的，新会话从零起（后端的 InMemorySaver 上下文也一起丢了）
  lastTurn.value = null
  sessionUsage.value = { inputTokens: 0, outputTokens: 0, cachedTokens: 0 }
}
