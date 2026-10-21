<script setup lang="ts">
/**
 * 「检索结果确认」面板（Phase 9）。
 *
 * 检索（关键词 + 时间范围）来自 MCP，相关度与日期都不完全可靠，所以在**提取之前**把候选篇目
 * 全部摊开，由用户勾选真正要抓的：勾中的才会走大模型提取，未勾选的由后端直接丢弃。
 *
 * 选择状态用「取消勾选的集合」（`deselected`）表示而不是「已勾选集合」：
 * 这样新出现的行默认就是选中的，也不需要在 `policies` 刷新时做额外的同步。
 * 例外（Phase 13）：**已在成果库**（`in_library`）与**判别标注不建议入库**（`judge_tagged`）的行
 * 默认取消勾选、置灰沉底——由 `watch(policies)` 补种进 `deselected`，且只补种用户没手动切换过的行。
 */
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { cancelTask, confirming, confirmSelection, policies, stopping, task } from '../store'
import type { PolicyResultItem } from '../api/types'

/** 用户**取消**勾选的篇目 id；不在这里的（含刚刷出来的新行）视为选中。 */
const deselected = ref<Set<number>>(new Set())

/** 用户主动切换过勾选的篇目：SSE 刷新重新补种「已在库默认不勾」时不再覆盖用户的选择。 */
const userToggled = new Set<number>()

const total = computed(() => policies.value.length)
const selectedIds = computed(() =>
  policies.value.filter((row) => !deselected.value.has(row.id)).map((row) => row.id),
)
const selectedCount = computed(() => selectedIds.value.length)
const allSelected = computed(() => selectedCount.value === total.value)
const noneSelected = computed(() => selectedCount.value === 0)

/** 沉底权重：建议入库 0 → 判别不建议 1 → 已在库 2（与后端三分区、stage 文案的顺序一致）。 */
function sinkRank(row: PolicyResultItem): number {
  if (row.in_library) return 2
  if (row.judge_tagged) return 1
  return 0
}

/** 展示顺序：建议入库在前（后端已按发布日期倒序），不建议入库与已在库的置灰沉底（sort 稳定，组内保持原序）。 */
const displayedPolicies = computed(() =>
  [...policies.value].sort((a, b) => sinkRank(a) - sinkRank(b)),
)
const keepCount = computed(
  () => policies.value.filter((row) => !row.in_library && !row.judge_tagged).length,
)
const taggedCount = computed(
  () => policies.value.filter((row) => !row.in_library && row.judge_tagged).length,
)
const libCount = computed(() => policies.value.filter((row) => row.in_library).length)

// 已在库 / 判别不建议的行默认不勾选：只在 policies 变化时补种，且不碰用户切换过的行；
// 其余新增行永远不进 deselected —— 「新行默认选中」的原有约定保持不变。
watch(
  policies,
  (rows) => {
    const next = new Set(deselected.value)
    for (const row of rows) {
      if ((row.in_library || row.judge_tagged) && !userToggled.has(row.id)) next.add(row.id)
    }
    deselected.value = next
  },
  { immediate: true },
)

const rangeText = computed(() => {
  const current = task.value
  if (!current || (!current.date_from && !current.date_to)) return '时间范围：不限'
  return `时间范围：${current.date_from ?? '不限'} ~ ${current.date_to ?? '不限'}`
})

function isSelected(id: number): boolean {
  return !deselected.value.has(id)
}

function toggle(id: number): void {
  const next = new Set(deselected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  deselected.value = next
  userToggled.add(id)
}

function selectAll(): void {
  deselected.value = new Set()
}

function selectNone(): void {
  deselected.value = new Set(policies.value.map((row) => row.id))
}

/**
 * 该篇的发布日期与所选时间范围是否相符。
 * 两者都是 `YYYY-MM-DD`，直接按字符串比较即可（ISO 日期字典序等于时间序）。
 */
function dateFlag(row: PolicyResultItem): 'out' | 'none' | null {
  const current = task.value
  if (!current) return null
  // 没有发布日期但有成文日期时，日期列显示成文日期（标「成文」），不再报「无日期」；
  // 成文日期不参与时间窗口判断（窗口是发布日期口径）。
  if (!row.publish_date) return row.signed_date ? null : 'none'
  if (current.date_from && row.publish_date < current.date_from) return 'out'
  if (current.date_to && row.publish_date > current.date_to) return 'out'
  return null
}

async function onConfirm(): Promise<void> {
  await confirmSelection(selectedIds.value)
}
</script>

<template>
  <section class="confirm-card">
    <header class="confirm-head">
      <div class="head-left">
        <h2 class="head-title">检索结果确认</h2>
        <span class="count-tag">共 {{ total }} 篇（建议入库 {{ keepCount }} · 不建议入库 {{ taggedCount }} · 已在库 {{ libCount }}）· 已选 {{ selectedCount }} 篇</span>
      </div>
      <div class="head-right">
        <button class="text-btn" type="button" :disabled="allSelected" @click="selectAll">
          全选
        </button>
        <button class="text-btn" type="button" :disabled="noneSelected" @click="selectNone">
          全不选
        </button>
      </div>
    </header>

    <p class="hint">
      检索结果按关键词返回，<strong>相关度与发布日期都不保证准确</strong>（{{ rangeText }}）。
      请只勾选真正要抓取的政策——未勾选的会被丢弃，勾中的才会调用大模型逐篇提取。
      已入成果库、判别标注「不建议入库」的篇目统一置灰沉底、默认不勾选——仍可手动勾选。
    </p>

    <div class="table-wrap">
      <div class="table-head">
        <div class="col check-col"></div>
        <div class="col title-col">政策标题</div>
        <div class="col date-col">日期</div>
        <div class="col link-col">原文链接</div>
      </div>

      <div
        v-for="row in displayedPolicies"
        :key="row.id"
        class="table-row"
        :class="{ off: !isSelected(row.id), dim: row.in_library || !!row.judge_tagged }"
        role="checkbox"
        :aria-checked="isSelected(row.id)"
        tabindex="0"
        @click="toggle(row.id)"
        @keydown.enter.prevent="toggle(row.id)"
        @keydown.space.prevent="toggle(row.id)"
      >
        <div class="col check-col">
          <input
            class="cb"
            type="checkbox"
            :checked="isSelected(row.id)"
            tabindex="-1"
            aria-hidden="true"
            @click.stop="toggle(row.id)"
          />
        </div>
        <div class="col title-col">
          <span class="row-title">{{ row.title }}</span>
          <span v-if="row.judge_tagged" class="flag judge" title="判别服务标注：不建议入库（默认不勾选，可手动勾选）">不建议入库</span>
        </div>
        <div class="col date-col">
          <span class="row-date" :class="{ fallback: !row.publish_date && row.signed_date }">
            {{ row.publish_date ?? row.signed_date ?? '—' }}
          </span>
          <span
            v-if="!row.publish_date && row.signed_date"
            class="flag signed"
            title="页面没有可解析的发布日期，此处显示原文落款/成文日期；它不参与时间范围筛选"
          >成文</span>
          <span
            v-else-if="row.publish_date_source === 'model'"
            class="flag model"
            title="页面结构未解析到发布日期，该日期由模型从页面内容提取，建议核对原文"
          >模型提取</span>
          <span v-if="row.in_library" class="flag lib">已在库</span>
          <span v-if="dateFlag(row) === 'out'" class="flag out">超出范围</span>
          <span v-else-if="dateFlag(row) === 'none'" class="flag none">无日期</span>
        </div>
        <div class="col link-col">
          <a
            v-if="row.source_url"
            class="row-link"
            :href="row.source_url"
            target="_blank"
            rel="noopener noreferrer"
            @click.stop
          >
            <AppIcon name="external-link" :size="12" />
            原文
          </a>
          <span v-else class="row-link muted">—</span>
        </div>
      </div>
    </div>

    <footer class="confirm-foot">
      <p class="foot-note">
        此处日期优先显示检索结果给的<strong>发布日期</strong>；没有发布日期时，灰字显示原文落款/成文日期并标「成文」，它不参与时间范围筛选。成文日期与发布日期是两个字段，互不顶替。标「模型提取」的日期由模型从页面内容补提，建议核对原文。
      </p>
      <div class="foot-actions">
        <button
          class="ghost-btn danger"
          type="button"
          :disabled="stopping || confirming"
          @click="cancelTask()"
        >
          <AppIcon name="square" :size="13" />
          <span>{{ stopping ? '放弃中…' : '放弃本次抓取' }}</span>
        </button>
        <button
          class="primary-btn"
          type="button"
          :disabled="noneSelected || confirming"
          @click="onConfirm"
        >
          <AppIcon name="circle-check" :size="14" />
          <span>{{ confirming ? '提交中…' : `确认抓取（${selectedCount} 篇）` }}</span>
        </button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.confirm-card {
  background: var(--card);
  border: 1px solid var(--blue-border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 0 0 3px var(--blue-soft);
}

.confirm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 12px;
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

.count-tag {
  padding: 4px 10px;
  background: var(--blue-bg);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--blue);
  line-height: normal;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.text-btn {
  padding: 4px 8px;
  background: none;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--blue);
  line-height: normal;
}

.text-btn:hover:not(:disabled) {
  background: var(--blue-soft);
}

.text-btn:disabled {
  color: var(--text-tertiary);
  cursor: default;
}

.hint {
  padding: 0 20px 14px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.hint strong {
  font-weight: 600;
  color: var(--accent);
}

.table-wrap {
  overflow-x: auto;
}

.table-head,
.table-row {
  display: flex;
  align-items: center;
  min-width: 720px;
}

.table-head {
  padding: 10px 20px;
  background: #f8f9fa;
  border-top: 1px solid var(--divider);
  border-bottom: 1px solid var(--divider);
}

.table-head .col {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-tertiary);
  line-height: normal;
}

.table-row {
  padding: 13px 20px;
  border-bottom: 1px solid var(--divider);
  cursor: pointer;
}

.table-row:hover {
  background: #fafbfc;
}

.table-row:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: -2px;
}

/* 取消勾选的行：整行压暗，一眼看出「这篇不抓」 */
.table-row.off .row-title,
.table-row.off .row-date {
  color: var(--text-tertiary);
  text-decoration: line-through;
}

/* 置灰沉底的行（已在库 / 判别不建议）：整行压灰（勾选状态仍可手动改变） */
.table-row.dim .row-title,
.table-row.dim .row-date {
  color: var(--text-tertiary);
}

.col {
  display: flex;
  align-items: center;
  gap: 6px;
}

.check-col {
  width: 30px;
  flex-shrink: 0;
}

.cb {
  width: 15px;
  height: 15px;
  margin: 0;
  accent-color: var(--blue);
  cursor: pointer;
}

.title-col {
  flex: 1 1 0;
  min-width: 0;
}

.date-col {
  width: 190px;
  flex-shrink: 0;
}

.link-col {
  width: 80px;
  flex-shrink: 0;
}

.row-title {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-date {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* 没有发布日期、显示原文成文日期：弱化一级，避免被当成发布日期 */
.row-date.fallback {
  color: var(--text-tertiary);
}

.flag {
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  line-height: normal;
  white-space: nowrap;
}

.flag.out {
  background: var(--red-bg);
  color: var(--accent);
}

.flag.none {
  background: #f1f2f4;
  color: var(--text-tertiary);
}

/* 模型补提的发布日期：与结果表/详情页同一套蓝色标注 */
.flag.model {
  background: var(--blue-bg);
  color: var(--blue);
}

/* 成文日期兜底：中性灰标，跟「模型提取」区分开 */
.flag.signed {
  background: #eef1f4;
  color: var(--text-tertiary);
}

/* 已在成果库：中性灰标 */
.flag.lib {
  background: #eef1f4;
  color: var(--text-tertiary);
}

/* 判别服务「不建议入库」：橙标提示（该行已置灰沉底、默认不勾选） */
.flag.judge {
  background: #fff4e5;
  color: #b45309;
}

.row-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--blue);
  text-decoration: none;
  white-space: nowrap;
}

.row-link.muted {
  color: var(--text-tertiary);
}

.confirm-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 20px;
}

.foot-note {
  font-size: 12px;
  color: var(--text-tertiary);
}

.foot-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

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

.ghost-btn.danger {
  color: var(--accent);
}

.ghost-btn.danger:hover:not(:disabled) {
  border-color: var(--accent);
}

.ghost-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.primary-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 16px;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
  white-space: nowrap;
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

@media (max-width: 640px) {
  .confirm-foot {
    flex-direction: column;
    align-items: stretch;
  }

  .foot-note {
    display: none;
  }
}
</style>
