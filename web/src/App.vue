<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './components/AppIcon.vue'
import TopBar from './components/TopBar.vue'
import ScheduleModal from './components/ScheduleModal.vue'
import {
  bootstrap,
  dismissToast,
  scheduleModalOpen,
  startTaskWatch,
  stopTaskWatch,
  toast,
} from './store'

/**
 * Esc 优先级：定时任务弹窗（全局） > 页面内组件自己的 Esc（CrawlPage 的抽屉等在各自组件里）。
 * 来源管理自 Phase 16 起是独立页面（/sources），不再有「Esc 关来源弹窗」。
 */
const route = useRoute()

/**
 * Agent 入口页走自己的外壳：它把页面锁成一屏高（滚动交给页面内部，底部输入框常驻），
 * 纵向不分组的间距也去掉。**横向的内边距与最大宽度仍然沿用 `.content`**——
 * 内容左右边界要和其它页对齐。顶栏与其它页共用（见 TopBar.vue）。
 */
const isAgent = computed(() => route.name === 'agent')

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && scheduleModalOpen.value) {
    scheduleModalOpen.value = false
  }
}

function runToastAction() {
  const action = toast.value?.retry
  dismissToast()
  action?.()
}

/** 提示图标随类型切换：成功=绿勾 / 失败=红叉 / 信息=蓝i */
const toastIcon = computed(() =>
  toast.value?.kind === 'success' ? 'circle-check' : toast.value?.kind === 'info' ? 'info' : 'circle-x',
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void bootstrap()
  // 定时任务到点触发、另一个标签页起的任务，都在本页面之外：轮询 /tasks/current 才能发现
  startTaskWatch()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  stopTaskWatch()
})
</script>

<template>
  <div class="page" :class="{ 'page--full': isAgent }">
    <TopBar />
    <!-- 路由页共用同一条页面外壳（内边距/最大宽度/纵向间距）：
         各页只负责自己内部的分组，不再各自重复这一段 -->
    <main class="content" :class="{ 'content--flush': isAgent }">
      <router-view />
    </main>

    <ScheduleModal />

    <div v-if="toast" class="toast" :class="`toast--${toast.kind}`" role="status">
      <AppIcon :name="toastIcon" :size="15" class="toast-icon" />
      <p class="toast-text">{{ toast.message }}</p>
      <button v-if="toast.retry" class="toast-retry" type="button" @click="runToastAction">
        {{ toast.actionLabel || '重试' }}
      </button>
      <button class="toast-close" type="button" aria-label="关闭提示" @click="dismissToast">
        <AppIcon name="x" :size="14" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Agent 入口页锁一屏高：内容超出时由页面内部的滚动区消化，页面本身不滚 */
.page--full {
  height: 100vh;
  overflow: hidden;
}

/* 路由页的公共外壳：四个页面共用一套内边距与容器宽度 */
.content {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 80px 48px;
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
}

/* Agent 入口页只改纵向：撑满顶栏以下的一屏、不留分组间距；
   底部留白略微收紧，让常驻页底的输入框不显得悬空（横向不动，仍与其它页对齐） */
.content--flush {
  flex: 1;
  min-height: 0;
  gap: 0;
  padding-bottom: 28px;
}

/* 页面右下角常驻弹出（不挡顶部 tab 与页头），从右侧滑入；浅色卡片风，
   类型决定左侧图标的颜色（见下方 toast--*） */
.toast {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: min(680px, calc(100vw - 48px));
  padding: 12px 14px;
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px #1f23291f;
  animation: toast-slide 0.2s ease-out;
}

@keyframes toast-slide {
  from {
    opacity: 0;
    transform: translateX(24px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.toast-icon {
  flex-shrink: 0;
}

.toast--success .toast-icon {
  color: var(--green);
}

.toast--error .toast-icon {
  color: var(--accent);
}

.toast--info .toast-icon {
  color: var(--blue);
}

.toast-text {
  flex: 1 1 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-primary);
}

.toast-retry {
  padding: 5px 12px;
  background: var(--accent);
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
  white-space: nowrap;
}

.toast-close {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  background: none;
  border: none;
  color: var(--text-tertiary);
  line-height: normal;
}

.toast-close:hover {
  color: var(--text-primary);
}

/* 两处窄屏断点里 `.content--flush` 要重复一次：简写的 padding 会盖掉上面的
   `padding-bottom`（同为单类选择器，后写者胜） */
@media (max-width: 1280px) {
  .content {
    padding: 24px 32px 40px;
  }

  .content--flush {
    padding-bottom: 28px;
  }
}

@media (max-width: 640px) {
  .toast {
    right: 16px;
    bottom: 16px;
    max-width: calc(100vw - 32px);
  }

  .content {
    padding: 16px 16px 32px;
  }

  .content--flush {
    padding-bottom: 20px;
  }
}
</style>
