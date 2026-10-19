<script setup lang="ts">
/**
 * 抓取任务页（Phase 16 自 App.vue 拆出）：抓取配置 + 进度 + 确认面板/结果表 + 单篇抽屉。
 * 原来 App.vue 用 store.view 切页时这些组件常驻；路由化后它们属于本路由，
 * 组件随路由卸载，但数据全在 store（模块级），切换页面不丢。
 * 注意：任务 SSE 订阅是 store 闭包里的状态，**不受本组件卸载影响**——
 * 切到成果库再切回来，进度流不会断（ProgressPanel 只读 store，重挂载即恢复显示）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ConfigPanel from '../components/ConfigPanel.vue'
import ProgressPanel from '../components/ProgressPanel.vue'
import ConfirmPanel from '../components/ConfirmPanel.vue'
import ResultTable from '../components/ResultTable.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import {
  canCancel,
  cancelTask,
  enabledScheduleCount,
  form,
  ingestMode,
  openScheduleModal,
  phase,
  scheduleModalOpen,
  startTask,
  starting,
  stopping,
  task,
} from '../store'

const drawerPolicyId = ref<number | null>(null)
const drawerOpen = ref(false)

function openDrawer(policyId: number) {
  drawerPolicyId.value = policyId
  drawerOpen.value = true
}

function closeDrawer() {
  drawerOpen.value = false
}

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

function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  // 定时任务弹窗优先（全局弹窗，仍挂在 App.vue）
  if (scheduleModalOpen.value) return // App.vue 负责关它
  closeDrawer()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <main class="crawl">
    <!--
      本页页头：标题 + 页面级动作。这些按钮原先挂在全局顶栏右侧，各页长度不一，
      切页时会把顶栏的导航推来推去；顶栏现在只留导航（见 TopBar.vue）。
      任务状态不再重复展示——ProgressPanel 的卡片头已经带了同一枚状态胶囊。
    -->
    <header class="page-head">
      <div class="head-titles">
        <h1 class="head-title">采集任务</h1>
        <p class="head-sub">按关键词与筛选条件抓取国务院政策，确认候选篇目后批量提取入库</p>
      </div>
      <div class="head-actions">
        <span class="hint">{{ keywordHint }}</span>

        <button
          class="ghost-btn"
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
      </div>
    </header>

    <ConfigPanel />
    <ProgressPanel />
    <!-- 检索完成、等用户确认：确认面板取代结果表（同一批篇目，避免重复展示两遍） -->
    <ConfirmPanel v-if="phase === 'confirming'" />
    <ResultTable v-else @row-click="openDrawer" />

    <DetailDrawer :open="drawerOpen" :policy-id="drawerPolicyId" @close="closeDrawer" />
  </main>
</template>

<style scoped>
/* 页面外壳（内边距 / 最大宽度）由 App.vue 的 `.content` 提供，这里只管本页分组 */
.crawl {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ---------- 页头 ---------- */

.page-head {
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

.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* 次要操作（定时任务 / 终止）：描边按钮，与主按钮拉开层级 */
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
  white-space: nowrap;
}

.start-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

@media (max-width: 640px) {
  .hint {
    display: none;
  }
}
</style>
