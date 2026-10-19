<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'

const route = useRoute()
const router = useRouter()

/** 政策详情页也归属「政策库」这一项 */
const isLibraryFamily = computed(
  () => route.name === 'library' || route.name === 'library-detail',
)

/**
 * 顶部导航——对齐 design.pen「Agent 入口页（首页）」的顶部栏，**全站同一套**：
 * 比旧版多一个「政策助手」，且「抓取任务 / 成果库」按设计稿改名为「采集任务 / 政策库」。
 * 四页共用一条导航，「政策助手」切走再切回来不会再消失。
 */
const navItems = [
  { label: '政策助手', icon: 'sparkles', name: 'agent' },
  { label: '采集任务', icon: 'radar', name: 'crawl' },
  { label: '政策库', icon: 'database', name: 'library' },
  { label: '来源管理', icon: 'server', name: 'sources' },
] as const

const activeNav = computed(() => (isLibraryFamily.value ? 'library' : route.name))
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

    <nav class="page-tabs" role="tablist" aria-label="页面切换">
      <button
        v-for="item in navItems"
        :key="item.name"
        class="tab"
        :class="{ active: activeNav === item.name }"
        type="button"
        role="tab"
        :aria-selected="activeNav === item.name"
        @click="router.push({ name: item.name })"
      >
        <AppIcon :name="item.icon" :size="14" />
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <!--
      右侧占位：与左侧品牌等分剩余空间，四个 tab 才会恒定落在顶栏正中——
      早先这里挂的是页面级动作区，各页长度不一，切页时 tab 会左右跳动。
      那些动作已下放到各页自己的页头（CrawlPage / LibraryPage）。
    -->
    <div class="spacer" aria-hidden="true"></div>
  </header>
</template>

<style scoped>
/* 高度与内边距取设计稿的 60 / 0 20；不再用旧版的灰底胶囊容器 */
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 60px;
  flex-shrink: 0;
  padding: 0 20px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
}

/* 品牌与右侧占位都是 flex:1 / basis 0：两侧分到等宽空白，导航自然居中 */
.brand {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.spacer {
  flex: 1 1 0;
  min-width: 0;
}

/* 页面切换：政策助手 / 采集任务 / 政策库 / 来源管理 */
.page-tabs {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  /* 顶栏过窄时由导航自己横向滚动兜底（≤1100px 会整行换行，见下） */
  min-width: 0;
  overflow-x: auto;
}

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: none;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.tab svg {
  color: var(--text-tertiary);
}

.tab:hover {
  background: var(--bg);
}

/* 选中态：品牌色浅底 + 图标/文字转品牌色（与设计稿一致） */
.tab.active {
  background: var(--accent-soft);
  font-weight: 500;
  color: var(--primary);
}

.tab.active svg {
  color: var(--primary);
}

.logo {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
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

/* 窄屏：定高改自适应并让导航独占一行（四页共用一条导航，不能藏）。
   右侧占位留在品牌那一行，高度为 0，不影响换行后的布局。 */
@media (max-width: 1100px) {
  .topbar {
    height: auto;
    flex-wrap: wrap;
    gap: 12px;
    padding: 12px 16px;
  }

  .page-tabs {
    order: 3;
    width: 100%;
    overflow-x: auto;
  }
}

@media (max-width: 640px) {
  .subtitle {
    display: none;
  }
}
</style>
