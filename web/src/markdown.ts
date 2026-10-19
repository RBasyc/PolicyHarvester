/**
 * 极简 Markdown 渲染：marked 解析 + DOMPurify 消毒（对话助手的回复是 markdown）。
 *
 * 单独成一个模块、而不是写进 `MarkdownText.vue` 的 `<script setup>`，原因是下面两行
 * 配置**只能跑一次**：`marked.setOptions` / `DOMPurify.addHook` 都是往全局单例上挂东西，
 * 写在 `<script setup>` 里会**每个组件实例各跑一遍**（一屏几十条消息就是几十个 hook）。
 */

import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true }) // breaks：中文回复常用单换行分段

// 回复里的原文链接一律新标签页打开；rel 顺手断掉 window.opener
// 钩子的形参在 dompurify 各版本类型里是 `Node` 还是 `Element` 并不统一，所以先按
// nodeType 收窄成 Element 再用（两种类型下都成立）。
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.nodeType !== 1) return
  const el = node as Element
  if (el.tagName === 'A' && el.hasAttribute('href')) {
    el.setAttribute('target', '_blank')
    el.setAttribute('rel', 'noopener noreferrer')
  }
})

/**
 * 流式半截 markdown 的兜底：正文里代码围栏是**奇数**个时，末尾补一个收尾围栏。
 * 不补的话，最后那一段会被当成代码块渲染（整段等宽字体），闭合后自己会好——
 * 但流式过程中那一下很跳。
 */
function balanceFences(text: string): string {
  const fences = text.match(/^```/gm)?.length ?? 0
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text
}

/**
 * 渲染成**已消毒**的 HTML 串，供 `v-html` 使用。
 *
 * 消毒不是可选项：助手会转述抓回来的网页正文，那部分内容不可信。
 */
export function renderMarkdown(source: string): string {
  let html: string
  try {
    // 不传第二个参数：gfm / breaks 已在模块级 setOptions 里设过，而 marked 的
    // `async` 选项在各版本间增删过（v5 移除、后续又回来），传对象字面量可能撞上
    // 「未知属性」的类型报错。默认就是同步返回，断言成 string 即可。
    html = marked.parse(balanceFences(source)) as string
  } catch {
    html = source // 解析器都挂了：退回纯文本，总比整条气泡白屏好
  }
  return DOMPurify.sanitize(html)
}
