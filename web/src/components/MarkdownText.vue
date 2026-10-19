<script setup lang="ts">
import { computed } from 'vue'

import { renderMarkdown } from '../markdown'

/**
 * 助手回复的 markdown 渲染（marked + DOMPurify，见 `src/markdown.ts`）。
 *
 * 样式必须走 `:deep()`：`v-html` 插进来的节点不在本组件的 scoped 作用域里，
 * 普通选择器一个都匹配不上。字号/行高与用户气泡保持一致，只压一压块级元素的默认间距。
 */
const props = defineProps<{ source: string }>()

const html = computed(() => renderMarkdown(props.source))
</script>

<template>
  <div class="md" v-html="html"></div>
</template>

<style scoped>
.md {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  word-break: break-word;
}

/* 首尾元素不要外边距：气泡自己的 padding 已经在做这件事了 */
.md :deep(> :first-child) {
  margin-top: 0;
}

.md :deep(> :last-child) {
  margin-bottom: 0;
}

.md :deep(p) {
  margin: 8px 0;
}

.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 14px 0 6px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}

.md :deep(li) {
  margin: 3px 0;
}

.md :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.md :deep(code) {
  padding: 1px 5px;
  background: var(--bg);
  border-radius: 4px;
  font-size: 12.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.md :deep(pre) {
  margin: 10px 0;
  padding: 10px 12px;
  background: var(--bg);
  border-radius: 8px;
  overflow-x: auto;
}

.md :deep(pre code) {
  padding: 0;
  background: none;
}

.md :deep(blockquote) {
  margin: 8px 0;
  padding: 2px 0 2px 12px;
  border-left: 3px solid var(--border);
  color: var(--text-secondary);
}

.md :deep(table) {
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 13px;
}

.md :deep(th),
.md :deep(td) {
  padding: 5px 10px;
  border: 1px solid var(--border);
  text-align: left;
}

.md :deep(th) {
  background: var(--bg);
  font-weight: 600;
}

.md :deep(hr) {
  margin: 14px 0;
  border: none;
  border-top: 1px solid var(--border);
}
</style>
