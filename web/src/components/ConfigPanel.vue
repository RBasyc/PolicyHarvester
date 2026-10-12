<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { facets, facetsLoading, form, loadFacets, notify } from '../store'

const collapsed = ref(false)
const draftKeyword = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

/** 当前展开的下拉（发布机构 / 文种）；null = 两个都收起 */
const openMenu = ref<'agency' | 'doc_type' | null>(null)

function toggleMenu(which: 'agency' | 'doc_type') {
  openMenu.value = openMenu.value === which ? null : which
}

/** 点击下拉以外的任何地方就收起；多选下拉不因选中某项而关闭 */
function onDocMouseDown(event: MouseEvent) {
  if (!(event.target as HTMLElement).closest('.select-wrap')) openMenu.value = null
}

onMounted(() => document.addEventListener('mousedown', onDocMouseDown))
onUnmounted(() => document.removeEventListener('mousedown', onDocMouseDown))

type RangePreset = 'all' | 'week' | 'month' | 'year'

const rangePreset = computed<RangePreset>(() => {
  if (!form.date_from && !form.date_to) return 'all'
  const days = daysBetween(form.date_from, new Date())
  if (days === 6 || days === 7) return 'week'
  if (days === 29 || days === 30) return 'month'
  if (days >= 360 && days <= 366) return 'year'
  return 'all'
})

/** 折叠态标题旁的筛选摘要（如「工信部 · 国办发」），让用户不用展开也能看到已选条件 */
const summary = computed(() => {
  const bits: string[] = []
  for (const a of form.agencies) bits.push(a.length > 6 ? a.slice(0, 5) + '…' : a)
  bits.push(...form.doc_types)
  return bits.slice(0, 3).join(' · ') + (bits.length > 3 ? ` 等${bits.length}项` : '')
})

/** 已选机构（order 保持 facets 顺序，避免跳动） */
const selectedAgencies = computed(() => {
  const order = new Map(facets.value.agencies.map((a) => [a.name, a]))
  const known = form.agencies.filter((a) => order.has(a))
  const extra = form.agencies.filter((a) => !order.has(a))
  const knownSorted = [...known].sort(
    (a, b) => (order.get(a)?.count ?? 0) - (order.get(b)?.count ?? 0),
  )
  return [...knownSorted, ...extra].map((name) => ({
    name,
    count: order.get(name)?.count ?? null,
  }))
})

const selectedDocTypes = computed(() =>
  form.doc_types.map((name) => ({
    name,
    count: facets.value.doc_types.find((d) => d.name === name)?.count ?? null,
  })),
)

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
    void loadFacets()
    return
  }
  const days = preset === 'week' ? 6 : preset === 'month' ? 29 : 364
  const today = new Date()
  const from = new Date(today.getTime() - days * 86_400_000)
  form.date_from = isoDate(from)
  form.date_to = isoDate(today)
  void loadFacets()
}

function addKeyword() {
  const word = draftKeyword.value.trim()
  if (!word) return
  if (!form.keywords.includes(word)) form.keywords.push(word)
  draftKeyword.value = ''
  void loadFacets()
}

function removeKeyword(word: string) {
  form.keywords = form.keywords.filter((item) => item !== word)
  void loadFacets()
}

function toggleAgency(name: string) {
  const i = form.agencies.indexOf(name)
  if (i >= 0) form.agencies.splice(i, 1)
  else form.agencies.push(name)
}

function toggleDocType(name: string) {
  const i = form.doc_types.indexOf(name)
  if (i >= 0) form.doc_types.splice(i, 1)
  else form.doc_types.push(name)
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
    if (typeof data.date_from === 'string' || data.date_from === null) form.date_from = data.date_from
    if (typeof data.date_to === 'string' || data.date_to === null) form.date_to = data.date_to
    if (Array.isArray(data.keywords)) {
      form.keywords = [...new Set<string>(data.keywords.filter((k: unknown) => typeof k === 'string' && k.trim()))]
    }
    if (Array.isArray(data.agencies)) {
      form.agencies = [...new Set<string>(data.agencies.filter((k: unknown) => typeof k === 'string' && k.trim()))]
    }
    if (Array.isArray(data.doc_types)) {
      form.doc_types = [...new Set<string>(data.doc_types.filter((k: unknown) => typeof k === 'string' && k.trim()))]
    }
    notify(`已导入配置：${file.name}`)
    void loadFacets()
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
        <span v-if="summary" class="head-summary">{{ summary }}</span>
      </div>
      <button class="collapse" type="button" @click="collapsed = !collapsed">
        <span>{{ collapsed ? '展开' : '收起' }}</span>
        <AppIcon :name="collapsed ? 'chevron-down' : 'chevron-up'" :size="16" />
      </button>
    </header>

    <template v-if="!collapsed">
      <div class="field">
        <p class="label">时间范围</p>
        <div class="chips-row">
          <div class="date-box">
            <AppIcon name="calendar" :size="14" class="date-icon" />
            <input v-model="form.date_from" type="date" class="date-input" aria-label="开始日期" @change="loadFacets" />
          </div>
          <span class="to">至</span>
          <div class="date-box">
            <AppIcon name="calendar" :size="14" class="date-icon" />
            <input v-model="form.date_to" type="date" class="date-input" aria-label="结束日期" @change="loadFacets" />
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
          <button
            type="button"
            class="chip"
            :class="{ active: rangePreset === 'year' }"
            @click="applyPreset('year')"
          >
            近一年
          </button>
        </div>
      </div>

      <div class="field">
        <p class="label">关键词（可选，可添加多个）</p>
        <div class="tag-input">
          <span v-for="kw in form.keywords" :key="kw" class="tag">
            {{ kw }}
            <AppIcon name="x" :size="12" class="tag-close" @click="removeKeyword(kw)" />
          </span>
          <input
            v-model="draftKeyword"
            class="tag-field"
            type="text"
            placeholder="输入关键词，回车添加；留空则按下方筛选条件抓取最新发文"
            @keydown.enter.prevent="addKeyword"
          />
        </div>
      </div>

      <div class="filter-row">
        <div class="form-row">
          <div class="field grow">
            <p class="label">发布机构</p>
            <div class="select-wrap">
              <button
                type="button"
                class="select"
                :class="{ open: openMenu === 'agency' }"
                :aria-expanded="openMenu === 'agency'"
                @click="toggleMenu('agency')"
              >
                <span class="select-value">
                  <AppIcon name="building-2" :size="14" class="select-icon" />
                  <span class="select-text">
                    {{ form.agencies.length ? `已选 ${form.agencies.length} 项` : '全部发布机构' }}
                  </span>
                </span>
                <AppIcon name="chevron-down" :size="16" class="select-arrow" />
              </button>
              <div v-if="openMenu === 'agency'" class="menu">
                <p v-if="facetsLoading" class="menu-hint">加载中…</p>
                <p v-else-if="!facets.agencies.length" class="menu-hint">没有相关内容</p>
                <template v-else>
                  <button
                    v-for="opt in facets.agencies"
                    :key="opt.name"
                    type="button"
                    class="menu-item"
                    :class="{ active: form.agencies.includes(opt.name) }"
                    @click="toggleAgency(opt.name)"
                  >
                    <span class="menu-name">{{ opt.name }}</span>
                    <span v-if="opt.count" class="menu-count">{{ opt.count }}</span>
                    <AppIcon
                      v-if="form.agencies.includes(opt.name)"
                      name="check"
                      :size="14"
                      class="menu-check"
                    />
                  </button>
                </template>
                <p v-if="facets.source === 'fallback'" class="menu-foot">
                  实时名单暂不可用，已回退内置名单
                </p>
              </div>
            </div>
            <div v-if="selectedAgencies.length" class="chips-row">
              <span v-for="a in selectedAgencies" :key="a.name" class="tag accent">
                {{ a.name }}<template v-if="a.count"> · {{ a.count }}</template>
                <AppIcon name="x" :size="12" class="tag-close" @click="toggleAgency(a.name)" />
              </span>
            </div>
          </div>
          <div class="field grow">
            <p class="label">文种</p>
            <div class="select-wrap">
              <button
                type="button"
                class="select"
                :class="{ open: openMenu === 'doc_type' }"
                :aria-expanded="openMenu === 'doc_type'"
                @click="toggleMenu('doc_type')"
              >
                <span class="select-value">
                  <AppIcon name="file-text" :size="14" class="select-icon" />
                  <span class="select-text">
                    {{ form.doc_types.length ? `已选 ${form.doc_types.length} 项` : '全部文种' }}
                  </span>
                </span>
                <AppIcon name="chevron-down" :size="16" class="select-arrow" />
              </button>
              <div v-if="openMenu === 'doc_type'" class="menu">
                <p v-if="facetsLoading" class="menu-hint">加载中…</p>
                <p v-else-if="!facets.doc_types.length" class="menu-hint">没有相关内容</p>
                <template v-else>
                  <button
                    v-for="opt in facets.doc_types"
                    :key="opt.name"
                    type="button"
                    class="menu-item"
                    :class="{ active: form.doc_types.includes(opt.name) }"
                    @click="toggleDocType(opt.name)"
                  >
                    <span class="menu-name">{{ opt.name }}</span>
                    <span v-if="opt.count" class="menu-count">{{ opt.count }}</span>
                    <AppIcon
                      v-if="form.doc_types.includes(opt.name)"
                      name="check"
                      :size="14"
                      class="menu-check"
                    />
                  </button>
                </template>
                <p v-if="facets.source === 'fallback'" class="menu-foot">
                  实时名单暂不可用，已回退内置名单
                </p>
              </div>
            </div>
            <div v-if="selectedDocTypes.length" class="chips-row">
              <span v-for="d in selectedDocTypes" :key="d.name" class="tag accent">
                {{ d.name }}<template v-if="d.count"> · {{ d.count }}</template>
                <AppIcon name="x" :size="12" class="tag-close" @click="toggleDocType(d.name)" />
              </span>
            </div>
          </div>
        </div>
        <div class="hint-row">
          <AppIcon name="info" :size="12" class="hint-icon" />
          <p class="hint-text">
            输入关键词后，选项将按检索结果实时更新并标注数量；无匹配时提示「没有相关内容」；留空可直接浏览全部机构与文种
          </p>
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
  min-width: 0;
}

.head-icon {
  color: var(--primary);
  flex-shrink: 0;
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.head-desc {
  font-size: 12px;
  color: var(--text-tertiary);
}

.head-summary {
  font-size: 12px;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 3px 8px;
  border-radius: 999px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40ch;
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
  align-items: flex-start;
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
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.select-wrap {
  position: relative;
}

.select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  text-align: left;
  line-height: normal;
}

.select:hover {
  border-color: var(--text-tertiary);
}

.select.open {
  border-color: var(--accent);
}

.select-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.select-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.select-text {
  font-size: 14px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select-arrow {
  color: var(--text-tertiary);
  flex-shrink: 0;
  transition: transform 0.15s;
}

.select.open .select-arrow {
  transform: rotate(180deg);
}

.menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 240px;
  overflow-y: auto;
  padding: 4px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  background: none;
  border: none;
  border-radius: 6px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: left;
  line-height: normal;
}

.menu-item:hover {
  background: #f1f2f4;
}

.menu-item.active {
  background: var(--accent-soft);
  color: var(--accent);
}

.menu-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-count {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

.menu-item.active .menu-count {
  color: var(--accent);
}

.menu-check {
  flex-shrink: 0;
}

.menu-hint {
  padding: 14px 10px;
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
}

.menu-foot {
  padding: 6px 10px;
  border-top: 1px solid var(--divider);
  font-size: 11px;
  color: var(--text-tertiary);
}

.hint-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.hint-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.hint-text {
  font-size: 12px;
  color: var(--text-tertiary);
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

.tag.accent {
  background: var(--accent-soft);
  color: var(--accent);
}

.tag-close {
  color: var(--text-tertiary);
  cursor: pointer;
}

.tag.accent .tag-close {
  color: var(--accent);
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
