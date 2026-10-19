import {
  createRouter,
  createWebHashHistory,
  createWebHistory,
  type RouteRecordRaw,
} from 'vue-router'
import AgentPage from './views/AgentPage.vue'
import CrawlPage from './views/CrawlPage.vue'
import SourcesPage from './views/SourcesPage.vue'
import LibraryPage from './components/LibraryPage.vue'
import LibraryDetailPage from './components/LibraryDetailPage.vue'

/**
 * 全站路由（Phase 16 起取代 store.view 的 v-if 切页）。
 *
 * 模式取舍：开发（pnpm dev）用 history——Vite dev server 自带 SPA fallback，
 * 刷新 /library/123 不会 404；构建产物可能由任意静态服务器托管（甚至 file://），
 * 一律用 hash 模式，不依赖服务端回退配置。
 *
 * `/library/:id(\\d+)` 的数字正则约束让 `/library/abc` 落到 404 兜底而不是
 * 被详情页当成坏 id（与后端 `/meta` 先于 `/{id}` 注册的坑同款思路）。
 */
const routes: RouteRecordRaw[] = [
  // Agent 入口页是设计里的「首页」，所以根路径落在它上面；想回到旧入口就把下面这行改回 '/crawl'
  { path: '/', redirect: '/agent' },
  { path: '/agent', name: 'agent', component: AgentPage },
  { path: '/crawl', name: 'crawl', component: CrawlPage },
  { path: '/library', name: 'library', component: LibraryPage },
  {
    path: '/library/:id(\\d+)',
    name: 'library-detail',
    component: LibraryDetailPage,
    props: (route) => ({ policyId: Number(route.params.id) }),
  },
  { path: '/sources', name: 'sources', component: SourcesPage },
  { path: '/:pathMatch(.*)*', redirect: '/agent' },
]

export const router = createRouter({
  history: import.meta.env.DEV ? createWebHistory() : createWebHashHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  },
})
