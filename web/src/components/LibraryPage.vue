<script setup lang="ts">
/**
 * 成果库页：跨任务沉淀的已提取政策。
 * 卡片网格 + 关键词/机构/文种/年份筛选 + 分页；点卡片进「政策详情页」。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import SelectMenu from './SelectMenu.vue'
import {
  libraryFilters,
  libraryItems,
  libraryLoading,
  libraryMeta,
  libraryPage,
  libraryPageSize,
  libraryTotal,
  loadLibraryPage,
  openLibraryPolicy,
  setLibraryFilter,
} from '../store'
import type { LibraryPolicyItem } from '../api/types'

const keywordInput = ref(libraryFilters.keyword)
let keywordTimer: ReturnType<typeof setTimeout> | null = null

/** 搜索框防抖 300ms，输入停止后才发请求 */
watch(keywordInput, (value) => {
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    if (value !== libraryFilters.keyword) setLibraryFilter({ keyword: value })
  }, 300)
})

onBeforeUnmount(() => {
  if (keywordTimer) clearTimeout(keywordTimer)
})

const totalPages = computed(() =>
  Math.max(1, Math.ceil(libraryTotal.value / libraryPageSize.value)),
)

/** 页码窗口：总页数少全展示，多的时候 1 … c-1 c c+1 … last */
const pageWindow = computed<(number | '…')[]>(() => {
  const total = totalPages.value
  const current = libraryPage.value
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages: (number | '…')[] = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  if (start > 2) pages.push('…')
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < total - 1) pages.push('…')
  pages.push(total)
  return pages
})

function onCard(item: LibraryPolicyItem) {
  openLibraryPolicy(item.id)
}

function hasFilter(): boolean {
  return Boolean(
    libraryFilters.keyword.trim() ||
      libraryFilters.agency ||
      libraryFilters.doc_type ||
      libraryFilters.year,
  )
}
</script>

<template>
  <div class="library">
    <!-- 库头部：标题 + 三项统计 -->
    <section class="lib-head">
      <div class="head-titles">
        <h1 class="head-title">政策成果库</h1>
        <p class="head-sub">跨抓取任务持久化沉淀 · 重新执行任务不影响已入库数据</p>
      </div>
      <div class="head-stats" v-if="libraryMeta">
        <div class="stat">
          <p class="stat-value">{{ libraryMeta.total }}</p>
          <p class="stat-label">总篇数</p>
        </div>
        <div class="stat">
          <p class="stat-value">{{ libraryMeta.agency_count }}</p>
          <p class="stat-label">覆盖机构</p>
        </div>
        <div class="stat">
          <p class="stat-value accent">{{ libraryMeta.month_new }}</p>
          <p class="stat-label">本月新增</p>
        </div>
      </div>
    </section>

    <!-- 筛选工具栏 -->
    <section class="toolbar">
      <div class="search">
        <AppIcon name="search" :size="14" />
        <input
          v-model="keywordInput"
          class="search-input"
          type="text"
          placeholder="搜索政策标题关键词…"
          maxlength="100"
        />
        <button
          v-if="keywordInput"
          class="search-clear"
          type="button"
          aria-label="清除搜索"
          @click="keywordInput = ''"
        >
          <AppIcon name="x" :size="12" />
        </button>
      </div>
      <SelectMenu
        :model-value="libraryFilters.agency"
        :options="libraryMeta?.agencies ?? []"
        placeholder="全部机构"
        @update:model-value="setLibraryFilter({ agency: $event })"
      />
      <SelectMenu
        :model-value="libraryFilters.doc_type"
        :options="libraryMeta?.doc_types ?? []"
        placeholder="全部文种"
        @update:model-value="setLibraryFilter({ doc_type: $event })"
      />
      <SelectMenu
        :model-value="libraryFilters.year"
        :options="libraryMeta?.years ?? []"
        placeholder="全部年份"
        @update:model-value="setLibraryFilter({ year: $event })"
      />
      <span class="toolbar-spacer"></span>
      <span class="sort-hint">
        <AppIcon name="arrow-down-wide-narrow" :size="14" />
        按发布日期
      </span>
    </section>

    <!-- 卡片网格 -->
    <div v-if="libraryLoading" class="grid">
      <div v-for="i in 6" :key="i" class="card skeleton-card">
        <span class="skeleton skeleton-title"></span>
        <div class="skeleton-tags">
          <span class="skeleton skeleton-tag"></span>
          <span class="skeleton skeleton-tag"></span>
        </div>
        <span class="skeleton skeleton-line"></span>
        <span class="skeleton skeleton-line short"></span>
      </div>
    </div>

    <div v-else-if="libraryItems.length" class="grid">
      <article
        v-for="item in libraryItems"
        :key="item.id"
        class="card"
        role="button"
        tabindex="0"
        @click="onCard(item)"
        @keydown.enter="onCard(item)"
      >
        <h2 class="card-title">{{ item.title }}</h2>
        <div class="card-tags">
          <span v-if="item.agency" class="card-tag">{{ item.agency }}</span>
          <span v-if="item.doc_type" class="card-tag">{{ item.doc_type }}</span>
          <span class="card-tag-spacer"></span>
          <span v-if="item.publish_date" class="card-date">{{ item.publish_date }}</span>
        </div>
        <p class="card-summary">{{ item.summary ?? '暂无摘要' }}</p>
        <div class="card-foot">
          <span class="card-counts">
            <AppIcon name="list-checks" :size="12" />
            事实 {{ item.fact_count }} · 要点 {{ item.point_count }}
          </span>
          <span class="card-more">
            查看详情
            <AppIcon name="arrow-right" :size="12" />
          </span>
        </div>
      </article>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty">
      <AppIcon name="database" :size="28" class="empty-icon" />
      <p class="empty-title">{{ hasFilter() ? '没有符合条件的政策' : '成果库还是空的' }}</p>
      <p class="empty-desc">
        {{
          hasFilter()
            ? '换个关键词或筛选条件试试；筛选只作用于已提取成功的篇目'
            : '跑一次抓取任务并完成提取后，结果会自动沉淀到这里（按原文链接去重）'
        }}
      </p>
    </div>

    <!-- 分页 -->
    <div v-if="libraryTotal > 0" class="pagination">
      <span class="page-total">共 {{ libraryTotal }} 篇 · 每页 {{ libraryPageSize }} 篇</span>
      <div class="page-group">
        <button
          class="page-btn"
          type="button"
          :disabled="libraryPage <= 1 || libraryLoading"
          @click="loadLibraryPage(libraryPage - 1)"
        >
          上一页
        </button>
        <template v-for="(p, i) in pageWindow" :key="i">
          <span v-if="p === '…'" class="page-ellipsis">…</span>
          <button
            v-else
            class="page-btn num"
            :class="{ active: p === libraryPage }"
            type="button"
            :disabled="libraryLoading"
            @click="loadLibraryPage(p)"
          >
            {{ p }}
          </button>
        </template>
        <button
          class="page-btn"
          type="button"
          :disabled="libraryPage >= totalPages || libraryLoading"
          @click="loadLibraryPage(libraryPage + 1)"
        >
          下一页
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ---------- 库头部 ---------- */

.lib-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}

.head-titles {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.head-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: normal;
}

.head-sub {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
}

.head-stats {
  display: flex;
  align-items: center;
  gap: 24px;
}

.stat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: normal;
}

.stat-value.accent {
  color: var(--accent);
}

.stat-label {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

/* ---------- 筛选工具栏 ---------- */

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  flex-wrap: wrap;
}

.search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 280px;
  max-width: 100%;
  padding: 9px 12px;
  background: var(--bg);
  border-radius: 8px;
  color: var(--text-tertiary);
}

.search:focus-within {
  outline: 2px solid var(--accent-soft);
}

.search-input {
  flex: 1 1 0;
  min-width: 0;
  background: none;
  border: none;
  outline: none;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-primary);
}

.search-input::placeholder {
  color: var(--text-tertiary);
}

.search-clear {
  display: flex;
  align-items: center;
  padding: 2px;
  background: none;
  border: none;
  border-radius: 4px;
  color: var(--text-tertiary);
  line-height: normal;
}

.search-clear:hover {
  color: var(--text-primary);
}

.toolbar-spacer {
  flex: 1 1 0;
}

.sort-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* ---------- 卡片网格 ---------- */

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.card:hover {
  border-color: var(--text-tertiary);
  box-shadow: 0 4px 16px #0000000d;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
  color: var(--text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-tags {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-tag {
  padding: 3px 10px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.card-tag-spacer {
  flex: 1 1 0;
}

.card-date {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.card-summary {
  flex: 1 1 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.card-counts {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.card-more {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  white-space: nowrap;
}

/* ---------- 空状态 ---------- */

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 72px 24px;
  background: var(--card);
  border: 1px dashed var(--border);
  border-radius: 12px;
}

.empty-icon {
  color: var(--text-tertiary);
  margin-bottom: 4px;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  text-align: center;
  max-width: 460px;
  line-height: 1.6;
}

/* ---------- 分页 ---------- */

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.page-total {
  font-size: 12px;
  color: var(--text-tertiary);
}

.page-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.page-btn {
  padding: 7px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.page-btn:hover:not(:disabled):not(.active) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.page-btn.num {
  min-width: 32px;
  padding: 7px 0;
  text-align: center;
}

.page-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
  font-weight: 600;
}

.page-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.page-ellipsis {
  font-size: 13px;
  color: var(--text-tertiary);
  padding: 0 2px;
}

/* ---------- 骨架屏 ---------- */

.skeleton {
  display: inline-block;
  border-radius: 4px;
  background: linear-gradient(90deg, #e9ebee 25%, #f3f5f7 37%, #e9ebee 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.skeleton-card {
  cursor: default;
}

.skeleton-card:hover {
  border-color: var(--border);
  box-shadow: none;
}

.skeleton-title {
  width: 75%;
  height: 16px;
  border-radius: 6px;
}

.skeleton-tags {
  display: flex;
  gap: 8px;
}

.skeleton-tag {
  width: 64px;
  height: 20px;
  border-radius: 999px;
}

.skeleton-line {
  width: 100%;
  height: 12px;
}

.skeleton-line.short {
  width: 55%;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

@media (max-width: 640px) {
  .lib-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .head-stats {
    align-items: flex-start;
  }

  .stat {
    align-items: flex-start;
  }

  .search {
    width: 100%;
  }

  .toolbar-spacer {
    display: none;
  }

  .sort-hint {
    display: none;
  }
}
</style>
