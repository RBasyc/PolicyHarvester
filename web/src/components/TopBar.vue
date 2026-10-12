<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { form, startTask, starting, task } from '../store'
import type { TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
}

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: 'wait',
  running: 'run',
  completed: 'ok',
  failed: 'fail',
}

const label = computed(() => (task.value ? STATUS_LABEL[task.value.status] : '尚未抓取'))
const tone = computed(() => (task.value ? STATUS_TONE[task.value.status] : 'wait'))
const spinning = computed(() => task.value?.status === 'running')
const keywordHint = computed(() =>
  form.keywords.length ? `关键词 ${form.keywords.length} 个` : '未设置关键词',
)
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
        class="start-btn"
        type="button"
        :disabled="starting"
        :title="`抓取源：${form.source}`"
        @click="startTask()"
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
