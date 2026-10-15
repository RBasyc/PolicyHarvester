<script setup lang="ts">
/**
 * 来源列表（Phase 16 自 SourceModal 平移 + 增量）：启用/强制启用/停用/删除、
 * 行内展开验收报告（接入记录）、试运行（真实小批量抓取）、健康检查（逐入口真抓第 1 页）。
 *
 * 试运行/健康检查是页面化新增的入口：把 Agent 工具同门的
 * manage_source("run_trial"/"health") 暴露成了 HTTP 路由。
 */
import { computed, nextTick, reactive, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import SourceReport from '../SourceReport.vue'
import { api } from '../../api/client'
import type { SourceDTO, SourceOnboardingRun } from '../../api/types'
import { loadSources, notify, sources, sourcesLoading, syncTask } from '../../store'
import {
  busySourceId,
  enableSourceById,
  STATUS_LABEL,
} from '../../store/sourceImports'

const STATUS_TONE: Record<string, string> = {
  draft: 'wait',
  validated: 'wait',
  active: 'ok',
  disabled: 'off',
}

function text(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// ---------- 行内二次确认（删除 / 强制启用，同一时刻最多一行） ----------

type ConfirmAction = 'delete' | 'force'
const confirming = ref<{ id: number; action: ConfirmAction } | null>(null)

function isConfirming(id: number | null | undefined, action: ConfirmAction): boolean {
  return confirming.value?.id === id && confirming.value.action === action
}

// ---------- 历史接入记录（行内展开验收报告） ----------

const archiveId = ref<number | null>(null)
const archiveRuns = ref<SourceOnboardingRun[]>([])
const archiveLoading = ref(false)
let archiveEl: HTMLElement | null = null

/** 函数式 ref（元素在 v-for 里，拿不到稳定模板 ref）：只认真实 DOM 节点。 */
function setArchiveEl(el: unknown) {
  archiveEl = el instanceof HTMLElement ? el : null
}

const archiveDryRun = computed(
  () => archiveRuns.value.find((run) => run.kind === 'dry_run') ?? null,
)

async function toggleArchive(source: SourceDTO) {
  if (source.id == null) return
  if (archiveId.value === source.id) {
    archiveId.value = null
    archiveRuns.value = []
    return
  }
  archiveId.value = source.id
  archiveRuns.value = []
  archiveLoading.value = true
  const requestedId = source.id
  try {
    const response = await api.sourceRuns(source.id)
    // 等待期间用户可能已收起或改展开另一行：结果只落在还展开着的那一行的头上
    if (archiveId.value !== requestedId) return
    archiveRuns.value = response.items
  } catch (error) {
    if (archiveId.value !== requestedId) return
    notify(`接入记录加载失败：${text(error)}`)
    archiveId.value = null
    return
  } finally {
    if (archiveId.value === requestedId) archiveLoading.value = false
  }
  // 报告渲染后把展开的面板滚进可视区：内容很高时行会被顶上去，看着像「点了没反应」
  await nextTick()
  archiveEl?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

// ---------- 启用 / 停用 / 删除 ----------

async function enableItem(source: SourceDTO, force = false) {
  if (source.id == null) return
  confirming.value = null
  await enableSourceById(source.id, source.label, force)
}

async function disableSource(source: SourceDTO) {
  if (source.id == null || busySourceId.value != null) return
  busySourceId.value = source.id
  try {
    await api.disableSource(source.id)
    notify(`已停用「${source.label}」——已抓的任务与成果不受影响`)
    if (archiveId.value === source.id) archiveId.value = null
    await loadSources()
  } catch (error) {
    notify(`停用失败：${text(error)}`)
  } finally {
    busySourceId.value = null
  }
}

async function removeSource(source: SourceDTO) {
  if (source.id == null || busySourceId.value != null) return
  busySourceId.value = source.id
  try {
    await api.deleteSource(source.id)
    notify(`已删除来源「${source.label}」`)
    if (archiveId.value === source.id) {
      archiveId.value = null
      archiveRuns.value = []
    }
    await loadSources()
  } catch (error) {
    notify(`删除失败：${text(error)}`)
  } finally {
    busySourceId.value = null
    confirming.value = null
  }
}

// ---------- 试运行 / 健康检查（Phase 16 新入口） ----------

const trialBusyId = ref<number | null>(null)

async function trialSource(source: SourceDTO) {
  if (source.id == null || trialBusyId.value != null) return
  trialBusyId.value = source.id
  try {
    const result = await api.trialSource(source.id, {})
    // 试抓是在**这张页面**发起的，任务却属于抓取页：store 的「当前任务」只在首屏读一次，
    // 不同步的话抓取页会一直显示「尚未抓取」，而这条任务其实正在跑（或正停在等确认）。
    // 同步失败只影响显示（任务本身已建好），所以单独兜住，别把「已发起」报成「失败」。
    try {
      await syncTask()
    } catch {
      /* 忽略：抓取页刷新一次即可对齐 */
    }
    notify(result.status || '试运行任务已发起，进度见抓取页')
  } catch (error) {
    notify(`试运行失败：${text(error)}`)
  } finally {
    trialBusyId.value = null
  }
}

const healthState = reactive({
  openId: null as number | null,
  loading: false,
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

async function toggleHealth(source: SourceDTO) {
  if (source.id == null) return
  if (healthState.openId === source.id) {
    healthState.openId = null
    healthState.result = null
    return
  }
  healthState.openId = source.id
  healthState.result = null
  healthState.loading = true
  try {
    const result = await api.healthSource(source.id)
    if (healthState.openId !== source.id) return
    healthState.result = result
  } catch (error) {
    notify(`健康检查失败：${text(error)}`)
    healthState.openId = null
  } finally {
    healthState.loading = false
  }
}

const emit = defineEmits<{ switchTab: [tab: 'import'] }>()
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div class="panel-head-left">
        <AppIcon name="globe" :size="15" class="panel-icon" />
        <h2 class="panel-title">已接入来源</h2>
        <span class="panel-count">{{ sources.length }}</span>
      </div>
      <button class="primary" type="button" @click="emit('switchTab', 'import')">
        <AppIcon name="plus" :size="14" />
        <span>导入网址</span>
      </button>
    </header>

    <p v-if="sourcesLoading && !sources.length" class="empty">加载中…</p>
    <ul v-else class="source-list">
      <li v-for="item in sources" :key="item.key" class="source">
        <div class="source-main">
          <div class="source-title-row">
            <p class="source-name" :title="item.label">{{ item.label }}</p>
            <span class="tag" :class="STATUS_TONE[item.status]">
              {{ STATUS_LABEL[item.status] }}
            </span>
            <span v-if="item.builtin" class="tag wait">内置</span>
            <span v-if="!item.builtin && item.verified" class="tag ok">已验收</span>
          </div>
          <p class="source-meta">
            <code class="key">{{ item.key }}</code>
            <span class="dot">·</span>
            <template v-if="item.entrypoints.length">
              入口 {{ item.entrypoints.join('、') }}
              <span class="dot">·</span>
            </template>
            取值方式 <code class="key">{{ item.builtin ? 'mcp:gov_cn' : 'html_list' }}</code>
            <span class="dot">·</span>
            v{{ item.version }}
          </p>
          <p v-if="item.notes" class="source-notes">{{ item.notes }}</p>
        </div>

        <div class="source-actions">
          <button
            v-if="item.id != null"
            class="icon-btn"
            type="button"
            :class="{ on: archiveId === item.id }"
            :disabled="busySourceId === item.id"
            :title="archiveId === item.id ? '收起验收报告' : '展开验收报告'"
            @click="toggleArchive(item)"
          >
            <AppIcon :name="archiveId === item.id ? 'chevron-up' : 'chevron-down'" :size="14" />
          </button>

          <!-- 试运行：真实小批量抓取（仅已启用来源；走完整流水线，停在人工确认） -->
          <button
            v-if="item.id != null && item.status === 'active'"
            class="mini-btn"
            type="button"
            :disabled="trialBusyId === item.id"
            title="发起一次真实的小批量抓取（走完整流水线，停在人工确认；进度见抓取页）"
            @click="trialSource(item)"
          >
            {{ trialBusyId === item.id ? '发起中…' : '试运行' }}
          </button>

          <!-- 健康检查：逐入口真抓第 1 页 -->
          <button
            v-if="item.id != null"
            class="mini-btn"
            type="button"
            :class="{ on: healthState.openId === item.id }"
            :disabled="busySourceId === item.id"
            title="逐入口真抓第 1 页，检查可达性与条目解析"
            @click="toggleHealth(item)"
          >
            健康检查
          </button>

          <!-- 启用 / 强制启用：没过验收的来源只能走「强制启用」，且要二次确认（可取消） -->
          <template v-if="item.id != null && item.status !== 'active'">
            <button
              v-if="item.verified"
              class="mini-btn primary"
              type="button"
              :disabled="busySourceId === item.id"
              title="纳入抓取来源（有通过验收）"
              @click="enableItem(item)"
            >
              启用
            </button>
            <template v-else-if="isConfirming(item.id, 'force')">
              <button
                class="mini-btn danger"
                type="button"
                :disabled="busySourceId === item.id"
                title="未经试运行验收就投产，风险自负"
                @click="enableItem(item, true)"
              >
                确认强制启用
              </button>
              <button class="mini-btn" type="button" @click="confirming = null">取消</button>
            </template>
            <button
              v-else
              class="mini-btn"
              type="button"
              :disabled="busySourceId === item.id"
              title="这份配置没有通过验收：确认后可强制启用，或先展开验收报告重跑接入"
              @click="confirming = { id: item.id, action: 'force' }"
            >
              强制启用
            </button>
          </template>

          <button
            v-if="item.id != null && item.status === 'active'"
            class="mini-btn"
            type="button"
            :disabled="busySourceId === item.id"
            title="从抓取来源注册表摘掉；已抓的任务与成果不受影响"
            @click="disableSource(item)"
          >
            停用
          </button>

          <template v-if="item.id != null">
            <template v-if="isConfirming(item.id, 'delete')">
              <button
                class="mini-btn danger"
                type="button"
                :disabled="busySourceId === item.id"
                @click="removeSource(item)"
              >
                确认删除
              </button>
              <button class="mini-btn" type="button" @click="confirming = null">取消</button>
            </template>
            <button
              v-else
              class="icon-btn danger"
              type="button"
              :disabled="busySourceId === item.id"
              title="删除来源"
              @click="confirming = { id: item.id, action: 'delete' }"
            >
              <AppIcon name="trash-2" :size="14" />
            </button>
          </template>
        </div>

        <!-- 健康检查结果（行内展开） -->
        <div v-if="healthState.openId === item.id" class="inline-panel">
          <p class="inline-title">健康检查 · 逐入口真抓第 1 页</p>
          <p v-if="healthState.loading" class="empty">正在检查…</p>
          <template v-else-if="healthState.result?.entrypoints">
            <p class="inline-hint">
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
        </div>

        <!-- 历史接入记录（最近一次试运行的完整报告） -->
        <div v-if="archiveId === item.id" :ref="setArchiveEl" class="archive">
          <div class="archive-head">
            <p class="archive-title">
              <template v-if="archiveDryRun">
                最近一次试运行 · {{ archiveDryRun.finished_at ?? archiveDryRun.started_at }}
              </template>
              <template v-else>验收报告</template>
            </p>
            <button class="archive-collapse" type="button" @click="toggleArchive(item)">
              <AppIcon name="chevron-up" :size="13" />
              <span>收起</span>
            </button>
          </div>
          <p v-if="archiveLoading" class="empty">读取接入记录…</p>
          <SourceReport v-else-if="archiveDryRun" :report="archiveDryRun.report" />
          <p v-else class="empty">
            还没有试运行记录——这份来源是直接注册进来的，没有可复核的验收报告。
          </p>
        </div>
      </li>
    </ul>

    <p class="panel-hint">
      只有「已启用」的来源才会出现在抓取配置的来源下拉里；内置来源随服务发布，不能修改。
      「试运行」会发起一次真实抓取任务（可在抓取页看进度）；「健康检查」只读第 1 页，不产生任务。
    </p>
  </section>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.panel-icon {
  color: var(--primary);
  flex-shrink: 0;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.panel-count {
  font-size: 12px;
  color: var(--text-tertiary);
  background: #f1f2f4;
  padding: 2px 8px;
  border-radius: 999px;
}

.panel-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

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

.empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.source {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.source-main {
  flex: 1 1 260px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.source-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.source-name {
  min-width: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f2f4;
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 500;
  line-height: normal;
}

.tag.ok {
  background: var(--green-bg);
  color: var(--green);
}

.tag.off {
  background: var(--red-bg);
  color: var(--accent);
}

.tag.wait {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.source-meta {
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
}

.source-notes {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.source-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
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

.icon-btn.on {
  border-color: var(--accent);
  color: var(--accent);
}

.icon-btn.danger {
  color: var(--accent);
}

.mini-btn {
  padding: 6px 10px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.mini-btn:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.mini-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.mini-btn.primary {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.mini-btn.danger {
  background: var(--red-bg);
  border-color: var(--accent);
  color: var(--accent);
}

.mini-btn.on {
  border-color: var(--accent);
  color: var(--accent);
}

/* ---------- 行内展开面板（健康检查 / 接入记录） ---------- */

.inline-panel {
  flex: 1 1 100%;
  margin-top: 4px;
  padding: 12px 14px;
  background: #f7f8f9;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.inline-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.inline-hint {
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


/* ---------- 历史接入记录 ---------- */

.archive {
  flex: 1 1 100%;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--divider);
}

.archive-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.archive-title {
  min-width: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.archive-collapse {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 3px 8px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
  line-height: normal;
}

.archive-collapse:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

@media (max-width: 640px) {
  .source {
    flex-direction: column;
    align-items: stretch;
  }

  .source-actions {
    justify-content: flex-start;
  }
}
</style>
