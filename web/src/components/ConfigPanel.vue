<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { form, notify } from '../store'
import type { ContentType, ScheduleType } from '../api/types'

const collapsed = ref(false)
const draftKeyword = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const CONTENT_TYPES: { value: ContentType; label: string }[] = [
  { value: 'policy', label: '政策文件' },
  { value: 'notice', label: '通知公告' },
  { value: 'all', label: '全部' },
]

const SCHEDULES: { value: ScheduleType; label: string }[] = [
  { value: 'manual', label: '手动' },
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
]

type RangePreset = 'all' | 'week' | 'month'

const rangePreset = computed<RangePreset>(() => {
  if (!form.date_from && !form.date_to) return 'all'
  const days = daysBetween(form.date_from, new Date())
  if (days === 6 || days === 7) return 'week'
  if (days === 29 || days === 30) return 'month'
  return 'all'
})

function daysBetween(from: string | null, to: Date): number {
  if (!from) return -1
  const start = new Date(`${from}T00:00:00`)
  return Math.round((to.getTime() - start.getTime()) / 86_400_000)
}

function isoDate(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function applyPreset(preset: RangePreset) {
  if (preset === 'all') {
    form.date_from = null
    form.date_to = null
    return
  }
  const days = preset === 'week' ? 6 : 29
  const today = new Date()
  const from = new Date(today.getTime() - days * 86_400_000)
  form.date_from = isoDate(from)
  form.date_to = isoDate(today)
}

function addKeyword() {
  const word = draftKeyword.value.trim()
  if (!word) return
  if (!form.keywords.includes(word)) form.keywords.push(word)
  draftKeyword.value = ''
}

function removeKeyword(word: string) {
  form.keywords = form.keywords.filter((item) => item !== word)
}

function pickFile() {
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  try {
    const data = JSON.parse(await file.text())
    // 只回填认识的字段，其余忽略；避免坏配置把表单写成非法状态
    if (typeof data.source === 'string' && data.source.trim()) form.source = data.source.trim()
    if (['policy', 'notice', 'all'].includes(data.content_type)) form.content_type = data.content_type
    if (['manual', 'daily', 'weekly'].includes(data.schedule)) form.schedule = data.schedule
    if (typeof data.date_from === 'string' || data.date_from === null) form.date_from = data.date_from
    if (typeof data.date_to === 'string' || data.date_to === null) form.date_to = data.date_to
    if (Array.isArray(data.keywords)) {
      form.keywords = [...new Set<string>(data.keywords.filter((k: unknown) => typeof k === 'string' && k.trim()))]
    }
    notify(`已导入配置：${file.name}`)
  } catch {
    notify(`导入失败：${file.name} 不是合法的 JSON 配置文件`)
  }
}
</script>

<template>
  <section class="card config">
    <header class="config-head">
      <div class="head-left">
        <AppIcon name="settings-2" :size="16" class="head-icon" />
        <h2 class="head-title">抓取配置</h2>
        <p class="head-desc">通过表单设置，或从 JSON 配置文件导入</p>
      </div>
      <button class="collapse" type="button" @click="collapsed = !collapsed">
        <span>{{ collapsed ? '展开' : '收起' }}</span>
        <AppIcon :name="collapsed ? 'chevron-down' : 'chevron-up'" :size="16" />
      </button>
    </header>

    <template v-if="!collapsed">
      <div class="form-row">
        <div class="field grow">
          <p class="label">抓取源</p>
          <div class="select">
            <span class="select-value">{{ form.source }}</span>
            <AppIcon name="chevron-down" :size="16" class="select-arrow" />
          </div>
        </div>
        <div class="field grow">
          <p class="label">内容类型</p>
          <div class="segmented">
            <button
              v-for="item in CONTENT_TYPES"
              :key="item.value"
              type="button"
              class="segment"
              :class="{ active: form.content_type === item.value }"
              @click="form.content_type = item.value"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
      </div>

      <div class="form-row">
        <div class="field grow">
          <p class="label">时间范围</p>
          <div class="chips-row">
            <div class="date-box">
              <AppIcon name="calendar" :size="14" class="date-icon" />
              <input v-model="form.date_from" type="date" class="date-input" aria-label="开始日期" />
            </div>
            <span class="to">至</span>
            <div class="date-box">
              <AppIcon name="calendar" :size="14" class="date-icon" />
              <input v-model="form.date_to" type="date" class="date-input" aria-label="结束日期" />
            </div>
            <button
              type="button"
              class="chip"
              :class="{ active: rangePreset === 'all' }"
              @click="applyPreset('all')"
            >
              不限
            </button>
            <button
              type="button"
              class="chip"
              :class="{ active: rangePreset === 'week' }"
              @click="applyPreset('week')"
            >
              近 7 天
            </button>
            <button
              type="button"
              class="chip"
              :class="{ active: rangePreset === 'month' }"
              @click="applyPreset('month')"
            >
              近 30 天
            </button>
          </div>
        </div>
        <div class="field grow">
          <p class="label">抓取周期</p>
          <div class="chips-row">
            <button
              v-for="item in SCHEDULES"
              :key="item.value"
              type="button"
              class="chip"
              :class="{ active: form.schedule === item.value }"
              @click="form.schedule = item.value"
            >
              {{ item.label }}
            </button>
            <span class="demo-hint">Demo 仅展示，不执行定时任务</span>
          </div>
        </div>
      </div>

      <div class="field">
        <p class="label">关键词（可添加多个）</p>
        <div class="tag-input">
          <span v-for="kw in form.keywords" :key="kw" class="tag">
            {{ kw }}
            <AppIcon name="x" :size="12" class="tag-close" @click="removeKeyword(kw)" />
          </span>
          <input
            v-model="draftKeyword"
            class="tag-field"
            type="text"
            placeholder="输入关键词，回车添加"
            @keydown.enter.prevent="addKeyword"
          />
        </div>
      </div>

      <div class="divider"></div>

      <div class="config-foot">
        <button type="button" class="import-btn" @click="pickFile">
          <AppIcon name="upload" :size="14" />
          <span>导入配置（JSON）</span>
        </button>
        <p class="import-hint">支持 .json 文件，导入后将覆盖当前表单配置</p>
        <input
          ref="fileInput"
          class="file-input"
          type="file"
          accept="application/json,.json"
          @change="onFileChange"
        />
      </div>
    </template>
  </section>
</template>

<style scoped>
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}

.config {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.head-icon {
  color: var(--primary);
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.head-desc {
  font-size: 12px;
  color: var(--text-tertiary);
}

.collapse {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
}

.form-row {
  display: flex;
  gap: 16px;
  align-items: flex-end;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.grow {
  flex: 1 1 0;
}

.label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.select-value {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.select-arrow {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.segmented {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: #f1f2f4;
  border-radius: 8px;
  width: fit-content;
}

.segment {
  padding: 7px 16px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.segment.active {
  background: #ffffff;
  box-shadow: 0 1px 3px #0000001a;
  font-weight: 500;
  color: var(--text-primary);
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.date-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  white-space: nowrap;
}

.date-icon {
  color: var(--text-tertiary);
}

.date-input {
  width: 118px;
  border: none;
  outline: none;
  background: none;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
}

.to {
  font-size: 13px;
  color: var(--text-tertiary);
}

.chip {
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.chip.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 500;
  color: var(--accent);
}

.demo-hint {
  padding: 4px 8px;
  background: #f1f2f4;
  border-radius: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: normal;
  white-space: nowrap;
}

.tag-input {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  background: #f1f2f4;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-primary);
  line-height: normal;
}

.tag-close {
  color: var(--text-tertiary);
  cursor: pointer;
}

.tag-field {
  flex: 1 1 140px;
  min-width: 140px;
  border: none;
  outline: none;
  background: none;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
}

.tag-field::placeholder {
  color: var(--text-tertiary);
}

.divider {
  height: 1px;
  background: var(--divider);
}

.config-foot {
  display: flex;
  align-items: center;
  gap: 12px;
}

.import-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--primary);
  line-height: normal;
  white-space: nowrap;
}

.import-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

.file-input {
  display: none;
}

@media (max-width: 1024px) {
  .form-row {
    flex-direction: column;
    align-items: stretch;
  }

  .head-desc {
    display: none;
  }
}
</style>
