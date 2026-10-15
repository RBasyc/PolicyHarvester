<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
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
  <div class="page">
    <TopBar />
    <!-- 路由页共用同一条页面外壳（内边距/最大宽度/纵向间距）：
         各页只负责自己内部的分组，不再各自重复这一段 -->
    <main class="content">
      <router-view />
    </main>

    <ScheduleModal />

    <div v-if="toast" class="toast" role="status">
      <AppIcon name="circle-x" :size="15" class="toast-icon" />
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

.toast {
  position: fixed;
  left: 50%;
  bottom: 28px;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: min(680px, calc(100vw - 32px));
  padding: 12px 14px;
  background: #23262b;
  border-radius: 10px;
  box-shadow: 0 8px 24px #00000033;
}

.toast-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.toast-text {
  flex: 1 1 0;
  font-size: 13px;
  line-height: 1.5;
  color: #ffffff;
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
  color: #ffffff99;
  line-height: normal;
}

.toast-close:hover {
  color: #ffffff;
}

@media (max-width: 1280px) {
  .content {
    padding: 24px 32px 40px;
  }
}

@media (max-width: 640px) {
  .content {
    padding: 16px 16px 32px;
  }
}
</style>
