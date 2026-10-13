<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  canCancel,
  cancelTask,
  clearAll,
  clearing,
  enabledScheduleCount,
  form,
  goCrawl,
  goLibrary,
  ingestMode,
  libraryMeta,
  openScheduleModal,
  startTask,
  starting,
  stopping,
  task,
  view,
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
  if (ingestMode.value === 'schedule') {
    // 定时模式：不直接提交，打开弹窗配置调度参数（抓取配置取当前表单快照）
    openScheduleModal('create')
    return
  }
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
    <div class="page-tabs" role="tablist" aria-label="页面切换">
      <button
        class="tab"
        :class="{ active: view === 'crawl' }"
        type="button"
        role="tab"
        :aria-selected="view === 'crawl'"
        @click="goCrawl()"
      >
        <AppIcon name="radar" :size="13" />
        <span>抓取任务</span>
      </button>
      <button
        class="tab"
        :class="{ active: view !== 'crawl' }"
        type="button"
        role="tab"
        :aria-selected="view !== 'crawl'"
        @click="goLibrary()"
      >
        <AppIcon name="database" :size="13" />
        <span>成果库</span>
      </button>
    </div>
    <div class="actions">
      <!-- 抓取任务页：状态 + 定时任务入口 / 终止 / 开始抓取（清空按钮在成果库页） -->
      <template v-if="view === 'crawl'">
        <div class="status-tag" :class="tone">
          <AppIcon name="loader-circle" :size="12" :class="{ spin: spinning }" />
          <span>{{ label }}</span>
        </div>
        <span class="hint">{{ keywordHint }}</span>

        <button
          class="ghost-btn schedule-entry"
          type="button"
          title="查看与管理定时入库任务"
          @click="openScheduleModal('list')"
        >
          <AppIcon name="timer" :size="13" />
          <span>定时任务</span>
          <span v-if="enabledScheduleCount" class="schedule-badge">{{ enabledScheduleCount }}</span>
        </button>

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
          class="start-btn"
          type="button"
          :disabled="starting"
          :title="ingestMode === 'schedule' ? '用当前配置创建定时任务' : `抓取源：${form.source}`"
          @click="onStart"
        >
          <AppIcon :name="ingestMode === 'schedule' ? 'timer' : 'play'" :size="14" />
          <span>{{ ingestMode === 'schedule' ? '定时入库' : starting ? '提交中…' : '开始抓取' }}</span>
        </button>
      </template>

      <!-- 成果库 / 详情页：入库计数 + 清空（从抓取页移来，两步确认交互不变） -->
      <template v-else>
        <div class="status-tag">
          <AppIcon name="database" :size="12" />
          <span>已入库 {{ libraryMeta?.total ?? 0 }} 篇</span>
        </div>

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
      </template>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 32px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
}

/* 页面切换胶囊：抓取任务 / 成果库 */
.page-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-right: auto;
  padding: 3px;
  background: #eff0f3;
  border-radius: 9px;
}

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: none;
  border: none;
  border-radius: 7px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.tab:hover {
  color: var(--text-primary);
}

.tab.active {
  background: var(--card);
  font-weight: 600;
  color: var(--text-primary);
  box-shadow: 0 1px 2px #00000014;
}

.tab.active svg {
  color: var(--accent);
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

/* 次要操作（终止 / 清空）：描边按钮，与主按钮拉开层级 */
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

/* 定时任务入口徽标：启用中的任务数 */
.schedule-badge {
  min-width: 16px;
  padding: 1px 5px;
  background: var(--accent);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
  text-align: center;
  line-height: normal;
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
