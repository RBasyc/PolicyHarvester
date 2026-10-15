<script setup lang="ts">
/**
 * 接入记录（Phase 16）：跨来源的最近勘察/试运行记录（每来源取最新一条）。
 * 数据持久在 `source_onboarding_run`——服务重启、进程内作业被清理都不影响这里。
 * 展开行看当次完整报告（试运行报告用 SourceReport 渲染，其余展示原始报告 JSON）。
 */
import { computed, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import SourceReport from '../SourceReport.vue'
import { api } from '../../api/client'
import type { SourceRecentRun } from '../../api/types'

const KIND_LABEL: Record<string, string> = {
  inspect: '勘察',
  discover: '内容发现',
  sample: '样本',
  analyze: '结构分析',
  dry_run: '试运行',
  validate: '配置校验',
}

const KIND_TONE: Record<string, string> = {
  inspect: 'wait',
  discover: 'wait',
  sample: 'wait',
  analyze: 'wait',
  dry_run: 'run',
  validate: 'wait',
}

function statusLabel(run: SourceRecentRun): string {
  switch (run.status) {
    case 'passed':
      return '通过'
    case 'failed':
      return '未通过'
    case 'blocked':
      return '受阻'
    case 'running':
      return '进行中'
    default:
      return run.status
  }
}

function statusTone(run: SourceRecentRun): string {
  switch (run.status) {
    case 'passed':
      return 'ok'
    case 'failed':
      return 'fail'
    case 'blocked':
      return 'fail'
    case 'running':
      return 'run'
    default:
      return 'wait'
  }
}

function text(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/** 一行话总结：优先失败原因，其次 summary 里的结论字段，最后留空 */
function summaryLine(run: SourceRecentRun): string {
  if (run.failure_reason) return run.failure_reason
  const summary = run.summary ?? {}
  for (const key of ['conclusion', 'result', 'note', 'reason', 'message']) {
    const value = summary[key]
    if (typeof value === 'string' && value) return value
  }
  return ''
}

// ---------- 数据加载 ----------

type KindFilter = 'all' | 'inspect' | 'dry_run'
const kindFilter = ref<KindFilter>('all')
const items = ref<SourceRecentRun[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
/** 一次拉多少（每来源取最新一条，所以多取一些再折） */
const limit = ref(20)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const response = await api.sourceRunsRecent({
      limit: limit.value,
      kind: kindFilter.value === 'all' ? undefined : kindFilter.value,
    })
    items.value = response.items
  } catch (err) {
    error.value = text(err)
  } finally {
    loading.value = false
  }
}

function setFilter(next: KindFilter) {
  if (kindFilter.value === next) return
  kindFilter.value = next
  expandedId.value = null
  void load()
}

function loadMore() {
  limit.value += 20
  void load()
}

const expandedId = ref<number | null>(null)

function toggleExpand(run: SourceRecentRun) {
  expandedId.value = expandedId.value === run.id ? null : run.id
}

/** 试运行类报告用 SourceReport 渲染；其余（勘察等）给原始 JSON */
const expandedIsDryRun = computed(
  () => items.value.find((r) => r.id === expandedId.value)?.kind === 'dry_run',
)

// 面板随 SourcesPage 一起挂载（v-show 常驻）：进来就拉，切走再切回不自动重刷，
// 新记录从「导入接入」完成时刷新来源列表的动作可见
void load()
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <div class="panel-head-left">
        <AppIcon name="list-checks" :size="15" class="panel-icon" />
        <h2 class="panel-title">接入记录</h2>
        <span class="panel-count">{{ items.length }}</span>
      </div>
      <div class="kind-tabs" role="tablist" aria-label="记录类型过滤">
        <button
          class="kind-tab"
          :class="{ active: kindFilter === 'all' }"
          type="button"
          @click="setFilter('all')"
        >
          全部
        </button>
        <button
          class="kind-tab"
          :class="{ active: kindFilter === 'inspect' }"
          type="button"
          @click="setFilter('inspect')"
        >
          勘察
        </button>
        <button
          class="kind-tab"
          :class="{ active: kindFilter === 'dry_run' }"
          type="button"
          @click="setFilter('dry_run')"
        >
          试运行
        </button>
      </div>
    </header>

    <p class="panel-hint">
      每次勘察/试运行的完整报告都持久保存在这里（服务重启不丢）；「导入接入」里的实时进度
      只是进程内快照，丢失后回到这里复核。
    </p>

    <p v-if="loading && !items.length" class="empty">加载中…</p>
    <div v-else-if="error" class="error-line">
      <AppIcon name="circle-x" :size="13" />
      <span>接入记录加载失败：{{ error }}</span>
      <button class="mini-btn" type="button" @click="load">重试</button>
    </div>
    <p v-else-if="!items.length" class="empty">还没有接入记录——去「导入接入」跑一个网址试试。</p>

    <ul v-else class="runs">
      <li v-for="run in items" :key="run.id" class="run">
        <button class="run-main" type="button" @click="toggleExpand(run)">
          <div class="run-badges">
            <span class="tag" :class="KIND_TONE[run.kind] ?? 'wait'">{{ KIND_LABEL[run.kind] ?? run.kind }}</span>
            <span class="tag" :class="statusTone(run)">{{ statusLabel(run) }}</span>
          </div>
          <div class="run-info">
            <p class="run-source" :title="run.source_label">{{ run.source_label }}</p>
            <p class="run-summary">
              {{ formatTime(run.started_at) }}
              <template v-if="summaryLine(run)"> · {{ summaryLine(run) }}</template>
            </p>
          </div>
          <AppIcon
            :name="expandedId === run.id ? 'chevron-up' : 'chevron-down'"
            :size="14"
            class="run-caret"
          />
        </button>

        <div v-if="expandedId === run.id" class="run-detail">
          <SourceReport v-if="expandedIsDryRun" :report="run.report" />
          <template v-else>
            <p class="run-detail-hint">这类记录没有结构化验收判据，原始报告如下：</p>
            <pre class="run-json">{{ JSON.stringify(run.report ?? run.summary ?? {}, null, 2) }}</pre>
          </template>
        </div>
      </li>
    </ul>

    <div v-if="items.length >= limit" class="more-row">
      <button class="ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? '加载中…' : '加载更多' }}
      </button>
    </div>
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
  flex-wrap: wrap;
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

.kind-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: #eff0f3;
  border-radius: 9px;
}

.kind-tab {
  padding: 5px 12px;
  background: none;
  border: none;
  border-radius: 7px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.kind-tab:hover {
  color: var(--text-primary);
}

.kind-tab.active {
  background: var(--card);
  font-weight: 600;
  color: var(--text-primary);
  box-shadow: 0 1px 2px #00000014;
}

.empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0;
}

.error-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--red-bg);
  border-radius: 8px;
  font-size: 12px;
  color: var(--accent);
  line-height: 1.6;
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

.tag.fail {
  background: var(--red-bg);
  color: var(--accent);
}

.tag.run {
  background: var(--blue-bg);
  color: var(--blue);
}

.tag.wait {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.runs {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.run {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.run-main {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  background: var(--card);
  border: none;
  font-family: inherit;
  text-align: left;
  line-height: normal;
}

.run-main:hover {
  background: #fafbfc;
}

.run-badges {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.run-info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.run-source {
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-summary {
  min-width: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-caret {
  flex-shrink: 0;
  color: var(--text-tertiary);
}

.run-detail {
  border-top: 1px solid var(--divider);
  padding: 12px 14px;
  background: #fafbfc;
}

.run-detail-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.run-json {
  max-height: 320px;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  background: #f1f2f4;
  border-radius: 8px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.more-row {
  display: flex;
  justify-content: center;
}

.ghost {
  padding: 8px 14px;
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

.mini-btn {
  padding: 4px 10px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.mini-btn:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}
</style>
