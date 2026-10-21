<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

import AppIcon from '../components/AppIcon.vue'
import MarkdownText from '../components/MarkdownText.vue'
import {
  browserAvailable,
  browserEnabled,
  isEmpty,
  loadFeatures,
  messages,
  resetChat,
  retryLast,
  sendMessage,
  sending,
  stopGeneration,
  toggleBrowser,
} from '../store/chat'

// 功能开关（浏览器可用性）：后端不可用时静默隐藏，不影响对话
onMounted(() => void loadFeatures())

/**
 * Agent 入口页（首页）——design.pen 的 `Ry4ES` 帧，Phase 19 起接真实对话。
 *
 * 两种状态共用底部输入卡片：**空态**是欢迎语 + 建议问题（居中），**对话态**是消息列表
 * （贴顶、随内容增长）。状态全在 `store/chat.ts` 里——它持有 SSE 流，所以切到别的页
 * 再切回来，正在生成的回答不会断，本组件也**不需要** onBeforeUnmount 做任何清理。
 *
 * 设计稿里两处**有意不做**（用户 2026-10-16 确认）：① 顶栏右侧的用户头像等一切与账号
 * 相关的东西；② 左侧历史会话栏。会话记忆由后端 `InMemorySaver` 按 `thread_id` 负责，
 * 前端不留历史（刷新即新会话），「新对话」按钮换 id 即可。
 */

/** 输入卡片底部的能力标签：只在空态显示，进入对话后让位给「新对话」 */
const tools: { label: string; icon: string }[] = [
  { label: '政策检索', icon: 'search' },
  { label: '全文阅读', icon: 'file-text' },
  { label: '分面统计', icon: 'chart-column' },
  { label: '流式输出', icon: 'zap' },
]

const suggestions: { icon: string; text: string }[] = [
  { icon: 'newspaper', text: '查询最新发布的国务院政策文件，按发文日期排序列出' },
  { icon: 'file-text', text: '打开一份政策全文，提取发文机关、文号与核心条款' },
  { icon: 'chart-column', text: '按部门与年份统计发文数量，生成政策分面概览' },
  { icon: 'scale', text: '对比两段时期人工智能相关政策表述的变化' },
]

const draft = ref('')
const inputEl = ref<HTMLTextAreaElement | null>(null)
const scrollEl = ref<HTMLElement | null>(null)

/** 输入框自动增高：先归零再量 scrollHeight，否则只会越量越高 */
const MAX_INPUT_HEIGHT = 160

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`
}

/**
 * 手写 `@input` 而不是 `v-model` + `@input` 并存：两个 handler 在同一事件上谁先跑
 * 取决于编译器合并顺序，量到的是上一帧的高度（慢一拍）。这里先把值写回再量。
 */
function onInput(event: Event) {
  draft.value = (event.target as HTMLTextAreaElement).value
  autoGrow()
}

function onSend() {
  if (sending.value || !draft.value.trim()) return
  sendMessage(draft.value)
  draft.value = ''
  void nextTick(autoGrow) // 清空后缩回一行
}

function onKeydown(event: KeyboardEvent) {
  // 中文输入法里 Enter 是在选词：此时 keydown 的 key 也是 Enter，必须靠 isComposing 让开
  if (event.isComposing) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    onSend()
  }
}

/**
 * 自动滚底。`stopPropagation` 那套不需要——`.chat` 就是唯一的滚动容器，
 * 所以直接写 `scrollTop`（`scrollIntoView` 会连祖先一起滚，把整页带偏）。
 */
const NEAR_BOTTOM_PX = 120
let nearBottom = true
let lastCount = 0

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
}

watch(
  // 字符串当 watch 源：只有「条数」「最后一条的长度」或「浏览步骤数」真变了才触发
  () =>
    `${messages.value.length}:${messages.value.at(-1)?.content.length ?? 0}:${
      messages.value.at(-1)?.steps?.length ?? 0
    }`,
  async () => {
    // 条数变了 = 刚发送 / 刚重试，无条件贴底；只是内容变长则尊重用户翻历史的滚动位置
    if (messages.value.length !== lastCount) {
      lastCount = messages.value.length
      nearBottom = true
    }
    if (!nearBottom) return
    await nextTick()
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  },
)
</script>

<template>
  <div class="agent">
    <!-- 对话区：内容不足一屏时空态垂直居中；对话态贴顶，超出时在这一层滚动（输入框不跟着滚） -->
    <section ref="scrollEl" class="chat" @scroll="onScroll">
      <div v-if="isEmpty" class="chat-inner">
        <div class="welcome">
          <!-- 横幅 Logo：仓库 static/logo 的横版（复制到 web/public/brand/） -->
          <img
            class="welcome-logo"
            src="/brand/policyharvester-logo.png"
            alt="PolicyHarvester"
          />
          <h1>你好，我是政策研究助手</h1>
          <p class="welcome-sub">
            接入国务院政策检索工具链，支持最新政策查询、全文要点提取与分面统计，回答流式输出。
          </p>
        </div>

        <div class="suggestions">
          <button
            v-for="item in suggestions"
            :key="item.text"
            class="sug"
            type="button"
            @click="sendMessage(item.text)"
          >
            <span class="sug-icon">
              <AppIcon :name="item.icon" :size="16" />
            </span>
            <span class="sug-text">{{ item.text }}</span>
          </button>
        </div>
      </div>

      <div v-else class="messages">
        <div v-for="msg in messages" :key="msg.id" class="msg" :class="`msg--${msg.role}`">
          <span class="avatar" :class="{ 'avatar--thinking': msg.role === 'assistant' && msg.status === 'streaming' }">
            <AppIcon :name="msg.role === 'user' ? 'user' : 'sparkles'" :size="15" />
          </span>

          <div class="bubble">
            <!-- 用户气泡是纯文本（保留换行）；助手气泡走 markdown 渲染 -->
            <p v-if="msg.role === 'user'" class="plain">{{ msg.content }}</p>

            <!-- 调用过程：工具调用 + 被顶掉的中间文本；流式中展开，结束后收进可折叠列表 -->
            <details
              v-if="msg.role === 'assistant' && msg.steps && msg.steps.length"
              class="steps"
              :open="msg.status === 'streaming'"
            >
              <summary class="steps-title">
                调用过程（{{ msg.steps.length }} 步）
                <span v-if="msg.status === 'streaming'" class="steps-live">进行中</span>
              </summary>
              <ol class="steps-list">
                <li
                  v-for="(step, index) in msg.steps"
                  :key="`${index}-${step.name}`"
                  class="step"
                  :class="{ failed: step.status === 'done' && !step.ok }"
                >
                  <AppIcon
                    v-if="step.text"
                    name="message-square"
                    :size="12"
                    class="step-icon step-text"
                  />
                  <AppIcon
                    v-else-if="step.status === 'start'"
                    name="loader-circle"
                    :size="12"
                    class="spin step-icon"
                  />
                  <AppIcon
                    v-else-if="step.ok"
                    name="check"
                    :size="12"
                    class="step-icon step-ok"
                  />
                  <AppIcon v-else name="circle-x" :size="12" class="step-icon step-fail" />
                  <!-- 文本段只展示内容；工具步骤主显工具名，detail 为辅（浏览器步骤带 URL 等） -->
                  <span v-if="step.text" class="step-detail">{{ step.detail }}</span>
                  <template v-else>
                    <span class="step-name">{{ step.name }}</span>
                    <span v-if="step.detail" class="step-detail">{{ step.detail }}</span>
                  </template>
                </li>
              </ol>
            </details>

            <!-- 已发出但一个 token 都还没来：首轮工具调用前那 10–20 秒的静默就靠它兜 -->
            <div
              v-if="msg.role === 'assistant' && msg.status === 'streaming' && !msg.content"
              class="thinking"
            >
              <AppIcon name="loader-circle" :size="14" class="spin" />
              <span>正在思考…</span>
            </div>

            <MarkdownText v-else-if="msg.role === 'assistant'" :source="msg.content" />

            <p v-if="msg.status === 'stopped'" class="note">已停止</p>

            <div v-if="msg.status === 'error'" class="msg-error">
              <AppIcon name="circle-x" :size="13" />
              <span>{{ msg.error }}</span>
              <button class="retry-btn" type="button" @click="retryLast">重试</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 输入卡片：常驻页底（.agent 的 flex 列里排在滚动区之后），是唯一带阴影的容器 -->
    <div class="composer">
      <textarea
        ref="inputEl"
        class="composer-input"
        rows="1"
        :value="draft"
        placeholder="输入政策研究问题，例如：帮我梳理 2025 年以来人工智能相关的国务院政策，并总结各自的核心条款…"
        @input="onInput"
        @keydown="onKeydown"
      ></textarea>

      <div class="composer-tools">
        <!-- 左槽：「浏览网页」开关 + 空态能力标签 / 对话态「新对话」 -->
        <div class="tools-left">
          <button
            v-if="browserAvailable"
            class="browse-toggle"
            :class="{ active: browserEnabled }"
            type="button"
            :aria-pressed="browserEnabled"
            :disabled="sending"
            title="让 Agent 用无头浏览器打开任意公网站点（只读、遵守 robots.txt）"
            @click="toggleBrowser"
          >
            <AppIcon name="globe" :size="12" />
            <span>浏览网页</span>
          </button>

          <div v-if="isEmpty" class="tool-tags">
            <div v-for="tool in tools" :key="tool.label" class="tool-tag">
              <AppIcon :name="tool.icon" :size="12" />
              <span>{{ tool.label }}</span>
            </div>
          </div>
          <button v-else class="new-chat" type="button" @click="resetChat">
            <AppIcon name="plus" :size="13" />
            <span>新对话</span>
          </button>
        </div>

        <button
          v-if="!sending"
          class="send-btn"
          type="button"
          aria-label="发送"
          :disabled="!draft.trim()"
          @click="onSend"
        >
          <AppIcon name="arrow-up" :size="16" />
        </button>
        <button
          v-else
          class="send-btn send-btn--stop"
          type="button"
          aria-label="停止生成"
          @click="stopGeneration"
        >
          <AppIcon name="square" :size="14" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
 * 顶栏与页面外壳（内边距 / 最大宽度）都由外面提供：顶栏是共享的 TopBar，
 * 容器宽度沿用 App.vue 的 `.content`——本页只负责「中间滚动 + 底部输入框」两段。
 */
.agent {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.chat {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* ---------- 自绘滚动条 ---------- */

/*
 * 只作用于本页这两个滚动容器：`.chat`（对话区）与 `.composer-input`（长到 MAX_INPUT_HEIGHT 后）。
 * 三条约定，改之前先读：
 *   ① **轨道透明** —— 露出来的就是容器自己的底色（对话区 `--bg`，输入框 `--card`），不额外铺色块，
 *      所以「背景色一致」是靠不留痕，不是靠把轨道染成同一个色。
 *   ② **滑块比轨道窄** —— 2px 透明边框 + `background-clip: content-box` 内缩（视觉 6px，两侧各留 2px），
 *      比把轨道做窄更稳：轨道留着，滑块边缘的圆角不会被裁。
 *   ③ **只在 hover 加深** —— 空闲态刻意压到接近底色，长对话里不抢视线。
 * Firefox 只有 `scrollbar-width`/`scrollbar-color` 两个属性，取值与下面 Chromium 分支一致，
 * 所以色值抽成变量（同一处改，两边同步）。
 */
.chat,
.composer-input {
  --scroll-thumb: #d7dade;
  --scroll-thumb-hover: var(--text-tertiary);

  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
}

.chat::-webkit-scrollbar,
.composer-input::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

.chat::-webkit-scrollbar-track,
.composer-input::-webkit-scrollbar-track,
.chat::-webkit-scrollbar-corner,
.composer-input::-webkit-scrollbar-corner {
  background: transparent;
}

.chat::-webkit-scrollbar-thumb,
.composer-input::-webkit-scrollbar-thumb {
  background-color: var(--scroll-thumb);
  background-clip: content-box;
  border: 2px solid transparent;
  border-radius: 999px;
}

/* hover 用 background-color 而不是 background 简写：简写会把上面的 background-clip 重置回
   border-box，滑块会突然从 6px 涨满 10px，跳一下。 */
.chat::-webkit-scrollbar-thumb:hover,
.composer-input::-webkit-scrollbar-thumb:hover {
  background-color: var(--scroll-thumb-hover);
}

/* 空态：内容不足一屏时垂直居中（margin:auto 的关键点），超出一屏时从顶部滚动 */
.chat-inner {
  margin: auto 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

/* 行高刻意不设：design.pen 里主标题也没写 lineHeight，交给字体默认值 */
.welcome h1 {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-primary);
  text-align: center;
}

/* 横幅 Logo：图片自带留白，按 260px 宽展示；窄屏跟着缩 */
.welcome-logo {
  width: 260px;
  max-width: 100%;
  height: auto;
}

.welcome-sub {
  max-width: 620px;
  font-size: 14px;
  line-height: 1.7;
  text-align: center;
  color: var(--text-secondary);
}

/* ---------- 建议问题 ---------- */

.suggestions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.sug {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  text-align: left;
}

.sug:hover {
  border-color: var(--text-tertiary);
}

.sug-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--bg);
  border-radius: 8px;
  color: var(--text-secondary);
}

.sug-text {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-secondary);
}

/* ---------- 消息列表 ---------- */

/*
 * 对话态要**贴顶**，所以不能用 `.chat-inner` 的 `margin: auto`（几条消息会被垂直居中）。
 * `flex: 0 0 auto` 是这里的要点：`.chat` 是 flex 列，默认 `flex-shrink: 1` 会把超高的
 * 子元素压回容器高度、内容自己在内部溢出，外层就读不到真实高度、滚不动。
 */
.messages {
  flex: 0 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px 0 8px;
}

.msg {
  display: flex;
  gap: 10px;
}

/* 用户消息靠右：头像和气泡整体反向排 */
.msg--user {
  flex-direction: row-reverse;
}

.avatar {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--bg);
  border-radius: 8px;
  color: var(--text-secondary);
}

.bubble {
  max-width: min(760px, 100%);
  padding: 11px 15px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.msg--user .bubble {
  background: var(--accent-soft);
  border-color: transparent;
}

/* 用户输入按原样显示（换行有效），不做 markdown 解析 */
.plain {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ---------- 调用过程 ---------- */

.steps {
  margin-bottom: 8px;
  padding: 7px 10px;
  background: var(--bg);
  border-radius: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.steps-title {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  line-height: normal;
  color: var(--text-tertiary);
  list-style: none;
}

/* 收起时必须给一个可见的展开提示（原生 marker 被 list-style:none 去掉了） */
.steps-title::after {
  content: '展开';
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary);
}

.steps[open] > .steps-title::after {
  content: '收起';
}

.steps-live {
  color: var(--accent);
}

.steps-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 7px 0 0;
  padding: 7px 0 0;
  border-top: 1px solid var(--divider);
  list-style: none;
}

.step {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  line-height: 1.5;
}

.step.failed .step-detail {
  color: var(--text-tertiary);
  text-decoration: line-through;
}

.step-icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--text-tertiary);
}

.step-ok {
  color: var(--accent);
}

/* 中间文本段：对话图标 + 正文色，与工具步骤（次要色）区分开 */
.step-text {
  color: var(--text-secondary);
}

.step-fail {
  color: var(--text-tertiary);
}

.step-name {
  flex-shrink: 0;
  color: var(--text-primary);
  font-weight: 500;
}

.step-detail {
  min-width: 0;
  word-break: break-word;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}

.spin {
  animation: spin 0.9s linear infinite;
}

/*
 * 助手头像的「思考中」旋转：刻意用 ease-in-out 而不是线性——每转一圈有加速↔减速的
 * 呼吸感（用户 2026-10-20 反馈线性旋转像加载图标在空转）。只挂 `.avatar`，步骤里的
 * 小 loader 仍用线性 `.spin`（尺寸小，缓动看不出差别）。
 */
.avatar--thinking {
  animation: spin 1.6s cubic-bezier(0.45, 0, 0.55, 1) infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.msg-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--accent);
}

.retry-btn {
  padding: 3px 10px;
  background: none;
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 12px;
  color: var(--accent);
  line-height: normal;
  white-space: nowrap;
}

.retry-btn:hover {
  background: var(--accent-soft);
}

/* ---------- 底部输入卡片 ---------- */

.composer {
  flex-shrink: 0;
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 8px 24px #1f232914;
}

.composer-input {
  display: block;
  width: 100%;
  padding: 0;
  background: none;
  border: none;
  outline: none;
  resize: none;
  overflow-y: auto;
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-primary);
}

.composer-input::placeholder {
  color: var(--text-tertiary);
}

.composer-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tools-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 「浏览网页」开关：与 tool-tag 同一观感，激活时用 accent（scoped 样式，不复用
   ConfigPanel 的 .chip——那两个类名只在本组件内有效） */
.browse-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 6px 11px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-family: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.browse-toggle:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.browse-toggle.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.browse-toggle:disabled {
  opacity: 0.6;
  cursor: default;
}

.tool-tags {
  display: flex;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}

.tool-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 6px 11px;
  background: var(--bg);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.new-chat {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: none;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.new-chat:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.send-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--accent);
  border: none;
  border-radius: 999px;
  color: #ffffff;
  line-height: normal;
}

.send-btn:hover {
  background: var(--primary);
}

.send-btn:disabled {
  opacity: 0.45;
}

.send-btn:disabled:hover {
  background: var(--accent);
}

/* 生成中：同一个位置换成「停止」 */
.send-btn--stop,
.send-btn--stop:hover {
  background: var(--text-secondary);
}

@media (max-width: 900px) {
  .welcome h1 {
    font-size: 26px;
  }

  .suggestions {
    grid-template-columns: 1fr;
  }
}
</style>
