<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { booting, logs, task } from '../store'
import type { TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '未开始',
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

const status = computed<TaskStatus | null>(() => task.value?.status ?? null)
const statusLabel = computed(() => (status.value ? STATUS_LABEL[status.value] : '未开始'))
const statusTone = computed(() => (status.value ? STATUS_TONE[status.value] : 'wait'))
const progress = computed(() => task.value?.progress ?? 0)

const stageText = computed(() => {
  if (booting.value) return '正在读取任务状态…'
  if (!task.value) return '配置抓取参数后，点击右上角「开始抓取」获取政策原文'
  if (task.value.status === 'pending') return '任务已创建，等待开始抓取…'
  return task.value.stage ?? '—'
})

const startedText = computed(() => {
  const started = task.value?.started_at
  if (!started) return '尚未开始'
  return `开始于 ${formatDateTime(started)}`
})

const etaText = computed(() => {
  const eta = task.value?.eta_seconds
  if (eta == null) return null
  if (eta < 60) return `预计剩余约 ${Math.max(1, Math.round(eta))} 秒`
  return `预计剩余约 ${Math.round(eta / 60)} 分钟`
})

const statsText = computed(() => {
  const current = task.value
  if (!current) return null
  const parts = [
    `已抓取 ${current.fetched_count}/${current.total_count} 篇`,
    `已提取 ${current.extracted_count}/${current.total_count} 篇`,
    `失败 ${current.failed_count}`,
  ]
  if (etaText.value) parts.push(etaText.value)
  return parts.join(' · ')
})

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}
</script>

<template>
  <section class="card progress">
    <header class="progress-head">
      <div class="head-left">
        <h2 class="head-title">抓取进度</h2>
        <div class="status-tag" :class="statusTone">
          <AppIcon
            name="loader-circle"
            :size="12"
            :class="{ spin: status === 'running' }"
          />
          <span>{{ statusLabel }}</span>
        </div>
      </div>
      <p class="start-time">{{ startedText }}</p>
    </header>

    <div class="progress-line">
      <div class="track">
        <div class="fill" :class="statusTone" :style="{ width: `${progress}%` }"></div>
      </div>
      <span class="percent">{{ progress }}%</span>
    </div>

    <div class="stage">
      <AppIcon name="sparkles" :size="15" class="stage-icon" />
      <p class="stage-text" :class="{ error: status === 'failed' }">{{ stageText }}</p>
    </div>

    <p v-if="statsText" class="stats">{{ statsText }}</p>

    <div class="log-stream">
      <div v-if="!logs.length" class="log-empty">
        {{ task ? '暂无日志' : '尚未开始抓取' }}
      </div>
      <div v-for="(line, i) in logs" :key="i" class="log-line">
        <span class="log-time">{{ line.time }}</span>
        <span class="log-text" :class="`tone-${line.tone}`">{{ line.message }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}

.progress {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.status-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: normal;
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

.start-time {
  font-size: 12px;
  color: var(--text-tertiary);
}

.progress-line {
  display: flex;
  align-items: center;
  gap: 12px;
}

.track {
  flex: 1 1 0;
  height: 8px;
  background: #edeff2;
  border-radius: 999px;
  overflow: hidden;
}

.fill {
  height: 8px;
  background: var(--accent);
  border-radius: 999px;
  transition: width 0.3s ease;
}

.fill.ok {
  background: var(--green);
}

.fill.fail {
  background: var(--accent);
}

.percent {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.stage {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.stage-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: normal;
}

.stage-text.error {
  color: var(--accent);
}

.stats {
  font-size: 12px;
  color: var(--text-tertiary);
}

.log-stream {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  background: var(--log-bg);
  border-radius: 10px;
  max-height: 200px;
  overflow: auto;
}

.log-empty {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--log-time);
}

.log-line {
  display: flex;
  gap: 10px;
  width: fit-content;
  min-width: 100%;
}

.log-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--log-time);
  white-space: nowrap;
}

.log-text {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--log-text);
}

.tone-success {
  color: var(--log-success);
}

.tone-active {
  color: #ffffff;
}

@media (max-width: 640px) {
  .progress-head {
    flex-wrap: wrap;
    gap: 8px;
  }

  .stage-text {
    white-space: normal;
    line-height: 1.5;
  }
}
</style>
