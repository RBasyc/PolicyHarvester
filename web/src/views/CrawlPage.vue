<script setup lang="ts">
/**
 * 抓取任务页（Phase 16 自 App.vue 拆出）：抓取配置 + 进度 + 确认面板/结果表 + 单篇抽屉。
 * 原来 App.vue 用 store.view 切页时这些组件常驻；路由化后它们属于本路由，
 * 组件随路由卸载，但数据全在 store（模块级），切换页面不丢。
 * 注意：任务 SSE 订阅是 store 闭包里的状态，**不受本组件卸载影响**——
 * 切到成果库再切回来，进度流不会断（ProgressPanel 只读 store，重挂载即恢复显示）。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ConfigPanel from '../components/ConfigPanel.vue'
import ProgressPanel from '../components/ProgressPanel.vue'
import ConfirmPanel from '../components/ConfirmPanel.vue'
import ResultTable from '../components/ResultTable.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import { phase, scheduleModalOpen } from '../store'

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
</style>
