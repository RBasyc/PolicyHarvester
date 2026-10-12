<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  canCancel,
  cancelTask,
  clearAll,
  clearing,
  form,
  startTask,
  starting,
  stopping,
  task,
} from '../store'
import type { TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '排队中',
  running: '运行中',
  awaiting_confirmation: '待确认',
  completed: '已完成',
  failed: '失败',
}

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: 'wait',
  running: 'run',
  awaiting_confirmation: 'wait',
  completed: 'ok',
  failed: 'fail',
}

const label = computed(() => (task.value ? STATUS_LABEL[task.value.status] : '尚未抓取'))
const tone = computed(() => (task.value ? STATUS_TONE[task.value.status] : 'wait'))
const spinning = computed(() => task.value?.status === 'running')
/** 停在「待确认」时后台并没有东西在跑，此时的「终止」语义上其实是「放弃本次抓取」。 */
const cancelLabel = computed(() =>
  task.value?.status === 'awaiting_confirmation' ? '放弃' : '终止',
)
const cancelTitle = computed(() =>
  task.value?.status === 'awaiting_confirmation'
    ? '放弃本次抓取（丢弃已检索到的候选篇目）'
    : '终止正在运行的抓取任务',
)
const keywordHint = computed(() =>
  form.keywords.length ? `关键词 ${form.keywords.length} 个` : '未设置关键词',
)

/**
 * 关键词是可选范围而非前置条件：留空时后端会按筛选条件（发布机构/文种/时间）浏览发文，
 * 所以这里不再拦截——用户「不输关键词、只看某机构近期发文」是合法用法。
 */
function onStart() {
  void startTask()
}

/** 「清空」是破坏性操作：先点一下变成确认态，再点「确认」才真的删。 */
const confirmingClear = ref(false)

async function onConfirmClear() {
  confirmingClear.value = false
  await clearAll()
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">
        <AppIcon name="wheat" :size="20" />
      </div>
      <div class="titles">
        <p class="product">PolicyHarvester</p>
        <p class="subtitle">国务院政策抓取 · 大模型结构化提取 Demo</p>
      </div>
    </div>
    <div class="actions">
      <div class="status-tag" :class="tone">
        <AppIcon name="loader-circle" :size="12" :class="{ spin: spinning }" />
        <span>{{ label }}</span>
      </div>
      <span class="hint">{{ keywordHint }}</span>

      <button
        v-if="canCancel"
        class="ghost-btn"
        type="button"
        :disabled="stopping"
        :title="cancelTitle"
        @click="cancelTask()"
      >
        <AppIcon name="square" :size="13" />
        <span>{{ stopping ? '处理中…' : cancelLabel }}</span>
      </button>

      <button
        v-if="!confirmingClear"
        class="ghost-btn danger"
        type="button"
        :disabled="!task || clearing"
        title="删除全部抓取任务与结果"
        @click="confirmingClear = true"
      >
        <AppIcon name="trash-2" :size="13" />
        <span>清空</span>
      </button>
      <div v-else class="confirm-group" role="alertdialog" aria-label="确认清空">
        <span class="confirm-text">清空全部数据？</span>
        <button
          class="ghost-btn danger"
          type="button"
          :disabled="clearing"
          @click="onConfirmClear()"
        >
          {{ clearing ? '清空中…' : '确认' }}
        </button>
        <button class="ghost-btn" type="button" @click="confirmingClear = false">取消</button>
      </div>

      <button
        class="start-btn"
        type="button"
        :disabled="starting"
        :title="`抓取源：${form.source}`"
        @click="onStart"
      >
        <AppIcon name="play" :size="14" />
        <span>{{ starting ? '提交中…' : '开始抓取' }}</span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 32px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary);
  border-radius: 9px;
  color: #ffffff;
}

.titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.product {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}

.status-tag.wait {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.status-tag.run {
  background: var(--blue-bg);
  color: var(--blue);
}

.status-tag.ok {
  background: var(--green-bg);
  color: var(--green);
}

.status-tag.fail {
  background: var(--red-bg);
  color: var(--accent);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* 次要操作（终止 / 清空）：描边按钮，与主按钮「开始抓取」拉开层级 */
.ghost-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.ghost-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.ghost-btn.danger {
  color: var(--accent);
}

.ghost-btn.danger:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.confirm-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.confirm-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
}

.start-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
}

.start-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

@media (max-width: 640px) {
  .topbar {
    flex-wrap: wrap;
    gap: 12px;
    padding: 14px 16px;
  }

  .subtitle,
  .hint {
    display: none;
  }
}
</style>
