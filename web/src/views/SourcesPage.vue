<script setup lang="ts">
/**
 * 来源管理页（Phase 16）：独立页面，三个模块用页内 tab 组织——
 *   list   来源列表（启用/停用/删除/试运行/健康检查 + 行内验收报告）
 *   import 导入接入（单条向导 + 批量导入队列）
 *   runs   接入记录（跨来源最近勘察/试运行，持久留痕）
 *
 * tab 与 URL query 双向同步（?tab=list|import|runs），支持深链：
 *   /sources?tab=import&url=<encoded>  从抓取页「导入网址」跳进来时预填地址。
 *
 * 三个模块用 v-show 常驻（切 tab 不销毁）：单条向导跑到一半切走再切回，进度还在。
 * 路由离开再回来由 `syncImportsOnMount` 对表重订阅（SSE 随组件卸载断开）。
 */
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import SourceListSection from '../components/sources/SourceListSection.vue'
import ImportSection from '../components/sources/ImportSection.vue'
import RunsSection from '../components/sources/RunsSection.vue'
import { loadSources } from '../store'
import { syncImportsOnMount, wizard } from '../store/sourceImports'

type Tab = 'list' | 'import' | 'runs'

const route = useRoute()
const router = useRouter()

const tab = computed<Tab>(() => {
  const value = route.query.tab
  return value === 'import' || value === 'runs' ? value : 'list'
})

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'list', label: '来源列表', icon: 'globe' },
  { key: 'import', label: '导入接入', icon: 'upload' },
  { key: 'runs', label: '接入记录', icon: 'list-checks' },
]

function switchTab(next: Tab) {
  router.replace({ query: { ...route.query, tab: next === 'list' ? undefined : next } })
}

onMounted(() => {
  void loadSources()
  void syncImportsOnMount()
  // 深链预填：/sources?tab=import&url=<encoded>（来自抓取页「导入网址」）
  const url = route.query.url
  if (tab.value === 'import' && typeof url === 'string' && url && wizard.step === 'form') {
    wizard.draft.url = url
    wizard.formError = null
  }
  // 清掉 url 参数：避免刷新页面把用户改过的输入覆盖回去
  if (typeof route.query.url === 'string') {
    router.replace({ query: { ...route.query, url: undefined } })
  }
})
</script>

<template>
  <div class="sources">
    <section class="page-head">
      <div class="head-titles">
        <h1 class="head-title">抓取来源管理</h1>
        <p class="head-sub">接入新网站 · 管理已有来源 · 复核接入记录（接入过程不经过大模型）</p>
      </div>
      <div class="page-tabs" role="tablist" aria-label="来源管理模块">
        <button
          v-for="item in TABS"
          :key="item.key"
          class="tab"
          :class="{ active: tab === item.key }"
          type="button"
          role="tab"
          :aria-selected="tab === item.key"
          @click="switchTab(item.key)"
        >
          <AppIcon :name="item.icon" :size="13" />
          <span>{{ item.label }}</span>
        </button>
      </div>
    </section>

    <SourceListSection v-show="tab === 'list'" @switch-tab="switchTab" />
    <ImportSection v-show="tab === 'import'" @switch-tab="switchTab" />
    <RunsSection v-show="tab === 'runs'" />
  </div>
</template>

<style scoped>
.sources {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.head-titles {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.head-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.head-sub {
  font-size: 13px;
  color: var(--text-tertiary);
  line-height: normal;
}

.page-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: #eff0f3;
  border-radius: 9px;
}

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  background: none;
  border: none;
  border-radius: 7px;
  font-family: inherit;
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

@media (max-width: 640px) {
  .page-head {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
