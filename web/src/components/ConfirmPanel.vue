<script setup lang="ts">
/**
 * 「检索结果确认」面板（Phase 9）。
 *
 * 检索（关键词 + 时间范围）来自 MCP，相关度与日期都不完全可靠，所以在**提取之前**把候选篇目
 * 全部摊开，由用户勾选真正要抓的：勾中的才会走大模型提取，未勾选的由后端直接丢弃。
 *
 * 选择状态用「取消勾选的集合」（`deselected`）表示而不是「已勾选集合」：
 * 这样新出现的行默认就是选中的，也不需要在 `policies` 刷新时做额外的同步。
 */
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { cancelTask, confirming, confirmSelection, policies, stopping, task } from '../store'
import type { PolicyResultItem } from '../api/types'

/** 用户**取消**勾选的篇目 id；不在这里的（含刚刷出来的新行）视为选中。 */
const deselected = ref<Set<number>>(new Set())

const total = computed(() => policies.value.length)
const selectedIds = computed(() =>
  policies.value.filter((row) => !deselected.value.has(row.id)).map((row) => row.id),
)
const selectedCount = computed(() => selectedIds.value.length)
const allSelected = computed(() => selectedCount.value === total.value)
const noneSelected = computed(() => selectedCount.value === 0)

const rangeText = computed(() => {
  const current = task.value
  if (!current || (!current.date_from && !current.date_to)) return '时间范围：不限'
  return `时间范围：${current.date_from ?? '不限'} ~ ${current.date_to ?? '不限'}`
})

function isSelected(id: number): boolean {
  return !deselected.value.has(id)
}

/** 来源目录的友好显示名（gongwen 公文 / bumenfile 部门文件 / gongbao 公报 / otherfile 政策解读） */
const CATALOG_LABEL: Record<string, string> = {
  gongwen: '公文',
  bumenfile: '部门文件',
  gongbao: '公报',
  otherfile: '政策解读',
}

function catalogLabel(catalog: string | null): string {
  if (!catalog) return '—'
  return CATALOG_LABEL[catalog] ?? catalog
}

function toggle(id: number): void {
  const next = new Set(deselected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  deselected.value = next
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
  if (!row.publish_date) return 'none'
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
        <span class="count-tag">共 {{ total }} 篇 · 已选 {{ selectedCount }} 篇</span>
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
    </p>

    <div class="table-wrap">
      <div class="table-head">
        <div class="col check-col"></div>
        <div class="col title-col">政策标题</div>
        <div class="col date-col">发布日期</div>
        <div class="col catalog-col">来源</div>
        <div class="col link-col">原文链接</div>
      </div>

      <div
        v-for="row in policies"
        :key="row.id"
        class="table-row"
        :class="{ off: !isSelected(row.id) }"
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
        </div>
        <div class="col date-col">
          <span class="row-date">{{ row.publish_date ?? '—' }}</span>
          <span v-if="dateFlag(row) === 'out'" class="flag out">超出范围</span>
          <span v-else-if="dateFlag(row) === 'none'" class="flag none">无日期</span>
        </div>
        <div class="col catalog-col">
          <span class="row-catalog">{{ catalogLabel(row.catalog) }}</span>
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
        日期取自检索结果，可能与原文实际日期有出入（提取时会用原文回填）。
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

.catalog-col {
  width: 80px;
  flex-shrink: 0;
}

.link-col {
  width: 80px;
  flex-shrink: 0;
}

.row-catalog {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
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
