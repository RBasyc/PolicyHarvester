<script setup lang="ts">
/**
 * 成果库页：跨任务沉淀的已提取政策。
 * 卡片网格 + 关键词/机构/文种/年份筛选 + 分页；点卡片进「政策详情页」。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import SelectMenu from './SelectMenu.vue'
import {
  clearAll,
  clearing,
  libraryFilters,
  libraryItems,
  libraryLoading,
  libraryMeta,
  libraryPage,
  libraryPageSize,
  libraryTotal,
  loadLibraryMeta,
  loadLibraryPage,
  setLibraryFilter,
} from '../store'
import type { LibraryPolicyItem } from '../api/types'

const router = useRouter()

/** 路由化后本页自加载（原来由 goLibrary 触发）：进页/刷新都拉最新统计与第一页 */
onMounted(() => {
  void loadLibraryMeta()
  void loadLibraryPage(1)
})

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
  router.push({ name: 'library-detail', params: { id: item.id } })
}

/**
 * 卡片摘要：摘要是大模型写的，常带换行/多段。在 `-webkit-line-clamp: 2` 下，
 * 一个换行会占掉两行配额里的一行——表现就是「换行了、正文看不见」。先压成一段再截。
 * 另外空串要落到「暂无摘要」：`??` 只挡 null/undefined，挡不住 `''`。
 */
function summaryOf(item: LibraryPolicyItem): string {
  return (item.summary ?? '').replace(/\s+/g, ' ').trim() || '暂无摘要'
}

function hasFilter(): boolean {
  return Boolean(
    libraryFilters.keyword.trim() ||
      libraryFilters.agency ||
      libraryFilters.doc_type ||
      libraryFilters.year,
  )
}

/**
 * 「清空」是**全库重置**（DELETE /api/tasks），不绑定任何单个任务：早先的
 * `!task` 守卫会让按钮在浏览器端 task 过期时永久置灰——成果库是跨任务沉淀的，
 * 而 task 只是最近一次抓取任务（清库后为 null、试运行/定时/另一个页面起的任务
 * 也不会同步过来），于是库里还有数据、按钮却点不动。空库点击是安全的：
 * 后端返回 deleted=0，前端提示「暂无可清空的数据」。
 * 破坏性操作：先点一下变成确认态，再点「确认」才真的删。
 */
const confirmingClear = ref(false)

async function onConfirmClear() {
  confirmingClear.value = false
  await clearAll()
}
</script>

<template>
  <div class="library">
    <!-- 库头部：标题 + 三项统计 + 全库重置（原先挂在全局顶栏右侧，见 TopBar.vue 的说明） -->
    <section class="lib-head">
      <div class="head-titles">
        <h1 class="head-title">政策成果库</h1>
        <p class="head-sub">跨抓取任务持久化沉淀 · 重新执行任务不影响已入库数据</p>
      </div>
      <div class="head-side">
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

        <button
          v-if="!confirmingClear"
          class="ghost-btn danger"
          type="button"
          :disabled="clearing"
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
          <!-- 来源打头：成果库跨来源混排，非 gov.cn 来源的机构/文种/日期常常
               全为空（地方站没有发文字号时推不出文种），少了这一枚卡片就只剩标题 -->
          <span v-if="item.source_name" class="card-tag source" :title="item.source_name">
            {{ item.source_name }}
          </span>
          <span v-if="item.agency" class="card-tag">{{ item.agency }}</span>
          <span
            v-if="item.agency && item.agency_source === 'model'"
            class="card-model"
            title="页面结构未解析到发文机关，该机关由模型从页面内容提取，建议核对原文"
          >模型提取</span>
          <span v-if="item.doc_type" class="card-tag">{{ item.doc_type }}</span>
          <span
            v-if="item.doc_type && item.doc_no_source === 'model'"
            class="card-model"
            title="页面结构未解析到文号，文种由模型补提的文号推导，建议核对原文"
          >模型提取</span>
          <span class="card-tag-spacer"></span>
          <span class="card-date" :class="{ none: !item.publish_date && !item.signed_date }">
            {{ item.publish_date ?? item.signed_date ?? '无日期' }}
            <span
              v-if="!item.publish_date && item.signed_date"
              class="card-date-signed"
              title="页面没有可解析的发布日期，此处显示原文落款/成文日期；它不参与时间筛选"
            >成文</span>
            <span
              v-else-if="item.publish_date_source === 'model'"
              class="card-date-model"
              title="页面结构未解析到发布日期，该日期由模型从页面内容提取，建议核对原文"
            >模型提取</span>
          </span>
        </div>
        <p class="card-summary">{{ summaryOf(item) }}</p>
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

/* 统计与「清空」同排右对齐：清空与统计隔开，避免误点 */
.head-side {
  display: flex;
  align-items: center;
  gap: 20px;
}

.head-stats {
  display: flex;
  align-items: center;
  gap: 24px;
}

/* 描边按钮（清空 / 确认 / 取消），与顶栏及各页页头保持同一套视觉 */
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

/* 三个下拉（SelectMenu 根节点）固定等宽：选中多机构联合发布的长名称时
   按钮内部省略号截断，工具栏布局保持稳定、不再被挤换行 */
.toolbar > .select {
  width: 180px;
  flex-shrink: 0;
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
  /* 一行标题也占满两行的高度：同一排卡片的标题/摘要/底栏才会逐行对齐 */
  min-height: 44px;
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
  /* 多部门联合发文的机关串很长：允许收缩 + 省略号截断，不能撑爆卡片 */
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 来源比机构/文种更容易长（「国务院政策文件库（gov.cn）」这种）：
   限个上界 + 省略号，避免把同一行的机构/文种挤没；完整名走 title 悬浮查看 */
.card-tag.source {
  max-width: 45%;
  flex-shrink: 1;
  color: var(--blue);
  background: var(--blue-bg);
}

.card-tag-spacer {
  flex: 1 1 0;
}

.card-date {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  flex-shrink: 0;
}

.card-date.none {
  color: var(--text-tertiary);
  opacity: 0.75;
}

.card-model {
  margin-left: -4px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--blue-bg);
  color: var(--blue);
  font-size: 10px;
  line-height: 1.7;
  white-space: nowrap;
}

.card-date-signed {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 999px;
  background: #eef1f4;
  color: var(--text-tertiary);
  font-size: 10px;
  line-height: 1.7;
  white-space: nowrap;
}

.card-date-model {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--blue-bg);
  color: var(--blue);
  font-size: 10px;
  line-height: 1.7;
}

/*
 * 摘要：**不能写 `flex: 1 1 0`**（这里原来就是，症状由此而来）。
 * `flex-basis: 0` 会让摘要不参与卡片的固有高度计算——一排卡片的高度由标题/标签/底栏
 * 决定，摘要只能分「剩下的空间」，而剩下多少取决于同一排里别人的标题占几行。
 * 于是：标题短的卡片摘要显示得下，标题满两行的卡片摘要被挤到 0 行（看着就是没摘要），
 * 中间态则是一行被截。表现正是「有时多一行、有时没有、有时换行了看不见」。
 * 改为按自身高度参与计算（最多两行），剩余空间交给底栏顶开。
 */
.card-summary {
  flex: 0 0 auto;
  /* 两行的固定槽位：没有摘要（或摘要只有一句）的卡片不会比别的矮一截 */
  min-height: 40px;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-foot {
  /* 卡片底部对齐：剩余高度在摘要与底栏之间，而不是接到摘要尾巴上 */
  margin-top: auto;
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

  /* 窄屏回退成左对齐的纵向堆叠：统计一行、清空按钮一行 */
  .head-side {
    width: 100%;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
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

  .toolbar > .select {
    width: auto;
    flex: 1 1 140px;
  }

  .toolbar-spacer {
    display: none;
  }

  .sort-hint {
    display: none;
  }
}
</style>
