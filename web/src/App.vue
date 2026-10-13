<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from './components/AppIcon.vue'
import TopBar from './components/TopBar.vue'
import ConfigPanel from './components/ConfigPanel.vue'
import ProgressPanel from './components/ProgressPanel.vue'
import ConfirmPanel from './components/ConfirmPanel.vue'
import ResultTable from './components/ResultTable.vue'
import DetailDrawer from './components/DetailDrawer.vue'
import LibraryPage from './components/LibraryPage.vue'
import LibraryDetailPage from './components/LibraryDetailPage.vue'
import { bootstrap, dismissToast, phase, toast, view } from './store'

const drawerPolicyId = ref<number | null>(null)
const drawerOpen = ref(false)

function openDrawer(policyId: number) {
  drawerPolicyId.value = policyId
  drawerOpen.value = true
}

function closeDrawer() {
  drawerOpen.value = false
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeDrawer()
}

function runToastAction() {
  const action = toast.value?.retry
  dismissToast()
  action?.()
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void bootstrap()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="page">
    <TopBar />
    <main v-if="view === 'crawl'" class="content">
      <ConfigPanel />
      <ProgressPanel />
      <!-- 检索完成、等用户确认：确认面板取代结果表（同一批篇目，避免重复展示两遍） -->
      <ConfirmPanel v-if="phase === 'confirming'" />
      <ResultTable v-else @row-click="openDrawer" />
    </main>
    <!-- 成果库两页：无路由，按 store.view 切换（与既有 phase 切换同一套模式） -->
    <main v-else-if="view === 'library'" class="content">
      <LibraryPage />
    </main>
    <main v-else class="content">
      <LibraryDetailPage />
    </main>

    <DetailDrawer :open="drawerOpen" :policy-id="drawerPolicyId" @close="closeDrawer" />

    <div v-if="toast" class="toast" role="status">
      <AppIcon name="circle-x" :size="15" class="toast-icon" />
      <p class="toast-text">{{ toast.message }}</p>
      <button v-if="toast.retry" class="toast-retry" type="button" @click="runToastAction">
        重试
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
