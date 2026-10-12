<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import TopBar from './components/TopBar.vue'
import ConfigPanel from './components/ConfigPanel.vue'
import ProgressPanel from './components/ProgressPanel.vue'
import ResultTable from './components/ResultTable.vue'
import DetailDrawer from './components/DetailDrawer.vue'

const drawerOpen = ref(false)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') drawerOpen.value = false
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="page">
    <TopBar />
    <main class="content">
      <ConfigPanel />
      <ProgressPanel />
      <ResultTable @row-click="drawerOpen = true" />
    </main>
    <DetailDrawer :open="drawerOpen" @close="drawerOpen = false" />
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
