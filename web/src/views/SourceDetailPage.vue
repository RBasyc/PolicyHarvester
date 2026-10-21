<script setup lang="ts">
/**
 * 来源详情页（/sources/:id）：版式对齐 design.pen「来源详情 · 试运行结果（优化版）」。
 *
 * 页头 = 返回 + 来源名/tag/元信息 + 操作（试运行 / 健康检查 / 停用·启用 / 删除）；
 * 主体 = 最近一次试运行的完整验收报告（SourceReport 新样式，三处共用）。
 * 数据来自 GET /sources/{id}（source + revisions + runs），健康检查是页头动作、
 * 结果行内落在报告上方。列表卡片不再行内展开报告——复核入口统一到这里。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import SourceReport from '../components/SourceReport.vue'
import { api } from '../api/client'
import type { SourceDetail, SourceOnboardingRun } from '../api/types'
import { loadSources, notify, notifySuccess, sources, syncTask } from '../store'
import {
  busySourceId,
  deleteSourceById,
  disableSourceById,
  enableSourceById,
  STATUS_LABEL,
} from '../store/sourceImports'

const props = defineProps<{ sourceId: number }>()

const router = useRouter()

const detail = ref<SourceDetail | null>(null)
const loading = ref(true)
const loadError = ref<string | null>(null)

/** 列表 store 里同一份来源（status/verified 口径与下拉一致）；详情接口失败时兜底展示。 */
const sourceDto = computed(
  () => sources.value.find((item) => item.id === props.sourceId) ?? detail.value?.source ?? null,
)

/** 最近一次试运行（runs 按时间倒序，取第一条 dry_run）。 */
const lastDryRun = computed<SourceOnboardingRun | null>(() => {
  const runs = detail.value?.runs ?? []
  return runs.find((run) => run.kind === 'dry_run') ?? null
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    detail.value = await api.getSource(props.sourceId)
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadSources()
  void load()
})

// ---------- 页头操作 ----------

const confirming = ref<null | 'delete' | 'force'>(null)

async function runTrial() {
  try {
    const result = await api.trialSource(props.sourceId, {})
    // 任务属于抓取页：同步「当前任务」，否则抓取页会一直显示「尚未抓取」
    try {
      await syncTask()
    } catch {
      /* 忽略：抓取页刷新一次即可对齐 */
    }
    notifySuccess(result.status || '试运行任务已发起，进度见抓取页')
  } catch (error) {
    notify(`试运行失败：${error instanceof Error ? error.message : String(error)}`)
  }
}

const healthState = reactive({
  loading: false,
  open: false,
  result: null as {
    loaded?: boolean
    entrypoints?: {
      name: string
      url: string
      reachable: boolean
      http_status?: number
      bytes?: number
      items?: number
      error?: string
    }[]
  } | null,
})

async function toggleHealth() {
  if (healthState.open) {
    healthState.open = false
    healthState.result = null
    return
  }
  healthState.open = true
  healthState.result = null
  healthState.loading = true
  try {
    healthState.result = await api.healthSource(props.sourceId)
  } catch (error) {
    notify(`健康检查失败：${error instanceof Error ? error.message : String(error)}`)
    healthState.open = false
  } finally {
    healthState.loading = false
  }
}

async function toggleActive() {
  const dto = sourceDto.value
  if (!dto || dto.id == null) return
  confirming.value = null
  if (dto.status === 'active') {
    const ok = await disableSourceById(dto.id, dto.label)
    if (ok) await load()
  } else {
    const ok = await enableSourceById(dto.id, dto.label)
    if (ok) await load()
  }
}

async function confirmForceEnable() {
  const dto = sourceDto.value
  if (!dto || dto.id == null) return
  confirming.value = null
  const ok = await enableSourceById(dto.id, dto.label, true)
  if (ok) await load()
}

async function confirmDelete() {
  const dto = sourceDto.value
  if (!dto || dto.id == null) return
  const ok = await deleteSourceById(dto.id, dto.label)
  if (ok) router.replace({ path: '/sources', query: { tab: 'list' } })
}

const ENGINE_LABEL: Record<string, string> = {
  html_list: '静态取数',
  browser_driven: '无头浏览器',
}

const busy = computed(() => busySourceId.value === props.sourceId)
</script>

<template>
  <div class="detail">
    <p v-if="loading && !detail" class="empty">读取来源详情…</p>
    <template v-else-if="sourceDto">
      <!-- 页头 -->
      <header class="head">
        <router-link class="back" :to="{ path: '/sources', query: { tab: 'list' } }">
          <AppIcon name="chevron-left" :size="13" />
          <span>返回来源管理</span>
        </router-link>
        <div class="head-main">
          <div class="head-row">
            <h1 class="head-title" :title="sourceDto.label">{{ sourceDto.label }}</h1>
            <span class="tag" :class="sourceDto.status === 'active' ? 'on' : 'off'">
              {{ STATUS_LABEL[sourceDto.status] }}
            </span>
            <span v-if="sourceDto.builtin" class="tag neutral">内置</span>
            <span v-if="!sourceDto.builtin" class="tag" :class="sourceDto.verified ? 'verified' : 'pending'">
              {{ sourceDto.verified ? '已验收' : '待验收' }}
            </span>
            <span v-if="sourceDto.engine === 'browser_driven'" class="tag neutral">无头浏览器</span>
            <div class="head-actions">
              <button
                v-if="sourceDto.status === 'active'"
                class="primary"
                type="button"
                :disabled="busy"
                title="发起一次真实的小批量抓取（走完整流水线，停在人工确认；进度见抓取页）"
                @click="runTrial"
              >
                <AppIcon name="play" :size="13" />
                <span>试运行</span>
              </button>
              <button
                v-if="sourceDto.engine !== 'browser_driven'"
                class="ghost"
                type="button"
                :class="{ on: healthState.open }"
                :disabled="busy"
                title="逐入口真抓第 1 页，检查可达性与条目解析"
                @click="toggleHealth"
              >
                <AppIcon name="activity" :size="13" />
                <span>健康检查</span>
              </button>
              <template v-if="!sourceDto.builtin">
                <button
                  v-if="confirming !== 'force'"
                  class="ghost"
                  type="button"
                  :disabled="busy"
                  :title="
                    sourceDto.status === 'active'
                      ? '从抓取来源注册表摘掉；已抓的任务与成果不受影响'
                      : sourceDto.verified
                        ? '纳入抓取来源（有通过验收）'
                        : '这份配置没有通过验收：确认后可强制启用，或回来源列表重跑接入'
                  "
                  @click="sourceDto.status === 'active' || sourceDto.verified ? toggleActive() : (confirming = 'force')"
                >
                  <AppIcon :name="sourceDto.status === 'active' ? 'ban' : 'power'" :size="13" />
                  <span>{{ sourceDto.status === 'active' ? '停用' : '启用' }}</span>
                </button>
                <template v-else>
                  <button class="ghost danger" type="button" :disabled="busy" @click="confirmForceEnable">
                    确认强制启用
                  </button>
                  <button class="ghost" type="button" @click="confirming = null">取消</button>
                </template>
                <button
                  v-if="confirming !== 'delete'"
                  class="icon-btn danger"
                  type="button"
                  :disabled="busy"
                  title="删除来源"
                  @click="confirming = 'delete'"
                >
                  <AppIcon name="trash-2" :size="14" />
                </button>
                <template v-else>
                  <button class="ghost danger" type="button" :disabled="busy" @click="confirmDelete">
                    确认删除
                  </button>
                  <button class="ghost" type="button" @click="confirming = null">取消</button>
                </template>
              </template>
            </div>
          </div>
          <p class="head-meta">
            <code class="key">{{ sourceDto.key }}</code>
            <span class="dot">·</span>
            <template v-if="sourceDto.entrypoints.length">
              入口 {{ sourceDto.entrypoints.join('、') }}
              <span class="dot">·</span>
            </template>
            取值方式 <code class="key">{{ ENGINE_LABEL[sourceDto.engine] ?? sourceDto.engine }}</code>
            <span class="dot">·</span>
            v{{ sourceDto.version }}
          </p>
        </div>
      </header>

      <!-- 健康检查结果（页头动作的行内结果） -->
      <section v-if="healthState.open" class="panel">
        <p class="panel-title">健康检查 · 逐入口真抓第 1 页</p>
        <p v-if="healthState.loading" class="empty">正在检查…</p>
        <template v-else-if="healthState.result?.entrypoints">
          <p class="panel-hint">
            进程内注册表：{{ healthState.result.loaded ? '已装载' : '未装载（未启用或已重启）' }}
          </p>
          <ul class="health-list">
            <li v-for="ep in healthState.result.entrypoints" :key="ep.name" class="health-row">
              <span class="tag" :class="ep.reachable ? 'ok' : 'off'">
                {{ ep.reachable ? '可达' : '不可达' }}
              </span>
              <span class="health-name">{{ ep.name }}</span>
              <span v-if="ep.reachable" class="health-meta">
                HTTP {{ ep.http_status }} · {{ ep.bytes }} 字节 · 解析出 {{ ep.items }} 条
              </span>
              <span v-else class="health-meta bad">{{ ep.error }}</span>
            </li>
          </ul>
        </template>
      </section>

      <!-- 最近一次试运行的验收报告 -->
      <p v-if="loadError" class="error-line">{{ loadError }}</p>
      <SourceReport
        v-if="lastDryRun?.report"
        :report="lastDryRun.report"
        :finished-at="lastDryRun.finished_at ?? lastDryRun.started_at"
      />
      <section v-else class="panel empty-panel">
        <template v-if="loading">
          <p class="empty">读取接入记录…</p>
        </template>
        <template v-else>
          <AppIcon name="info" :size="16" class="empty-icon" />
          <p class="empty-title">还没有试运行记录</p>
          <p class="empty-text">
            {{
              sourceDto.builtin
                ? '内置来源随服务发布，没有可复核的验收报告。'
                : '这份来源还没有可复核的试运行报告——启用后点上方「试运行」真实抓一批并校验正文，报告会落在这里。'
            }}
          </p>
        </template>
      </section>
    </template>
    <p v-else class="empty">{{ loadError ?? '来源不存在或已删除' }}</p>
  </div>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0;
}

.error-line {
  padding: 9px 12px;
  background: var(--red-bg);
  border-radius: 8px;
  font-size: 12px;
  color: var(--accent);
  line-height: 1.6;
}

/* ---------- 页头 ---------- */

.head {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  font-size: 12px;
  color: var(--text-tertiary);
  text-decoration: none;
}

.back:hover {
  color: var(--text-primary);
}

.head-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.head-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.head-title {
  min-width: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  line-height: normal;
}

.tag.on {
  background: var(--green-bg);
  color: var(--green);
}

.tag.off,
.tag.neutral {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.tag.verified {
  background: var(--blue-bg);
  color: var(--blue);
}

.tag.pending {
  background: var(--amber-soft);
  color: var(--amber);
}

.tag.ok {
  background: var(--green-bg);
  color: var(--green);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-wrap: wrap;
}

.head-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.dot {
  color: var(--text-tertiary);
}

.key {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  background: #f1f2f4;
  border-radius: 6px;
}

/* ---------- 按钮 ---------- */

.primary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
  white-space: nowrap;
}

.primary:disabled {
  opacity: 0.55;
  cursor: default;
}

.ghost {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.ghost:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.ghost:disabled {
  opacity: 0.5;
  cursor: default;
}

.ghost.on {
  border-color: var(--accent);
  color: var(--accent);
}

.ghost.danger {
  background: var(--red-bg);
  border-color: var(--accent);
  color: var(--accent);
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-secondary);
  line-height: normal;
}

.icon-btn:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.icon-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.icon-btn.danger {
  color: var(--accent);
}

/* ---------- 面板（健康检查 / 空状态） ---------- */

.panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.panel-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.health-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.health-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  flex-wrap: wrap;
}

.health-name {
  font-weight: 500;
  color: var(--text-primary);
}

.health-meta {
  color: var(--text-tertiary);
}

.health-meta.bad {
  color: var(--accent);
}

.empty-panel {
  align-items: flex-start;
  gap: 6px;
  padding: 24px 20px;
}

.empty-icon {
  color: var(--text-tertiary);
}

.empty-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-text {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.7;
}

@media (max-width: 720px) {
  .head-actions {
    margin-left: 0;
  }
}
</style>
