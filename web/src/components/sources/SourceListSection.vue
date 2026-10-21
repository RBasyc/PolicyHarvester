<script setup lang="ts">
/**
 * 来源列表（卡片版，对齐 design.pen「来源管理 · 入口卡片（优化版）」）：
 *   名称行（来源名 + 状态/验收 tag）→ 元信息行（代码 chip · 入口 · 取值方式 · 版本）
 *   → 摘要行（最近一次试运行的结论与时间）；
 *   右侧操作只留三个：启用开关（未验收的触发强制启用二次确认）、删除、进入详情。
 *
 * 试运行 / 健康检查 / 验收报告的行内展开已全部移到来源详情页（/sources/:id），
 * 列表不再展开——复核入口统一，卡片保持干净。
 * 摘要行数据：`sourceImports.recentDryRuns`（GET /sources/runs/recent?kind=dry_run 按 key 映射）。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../AppIcon.vue'
import type { SourceDTO, SourceRecentRun } from '../../api/types'
import { sources, sourcesLoading } from '../../store'
import {
  busySourceId,
  deleteSourceById,
  disableSourceById,
  enableSourceById,
  recentDryRuns,
  STATUS_LABEL,
} from '../../store/sourceImports'

const router = useRouter()
const emit = defineEmits<{ switchTab: [tab: 'import'] }>()

// ---------- 摘要行（最近一次试运行） ----------

type SummaryTone = 'ok' | 'fail' | 'block' | 'none'

function summaryOf(item: SourceDTO): { tone: SummaryTone; text: string } {
  if (item.builtin) return { tone: 'none', text: '内置来源 · 随服务发布' }
  const run: SourceRecentRun | undefined = recentDryRuns.value[item.key]
  if (!run) {
    return { tone: 'none', text: '尚未试运行——试运行验收通过并启用后才会进入抓取注册表' }
  }
  const report = run.report
  const when = String(run.finished_at ?? run.started_at ?? '').slice(0, 10)
  const datePart = when ? ` · 试运行于 ${when}` : ''
  if (!report) return { tone: 'none', text: `试运行没有产出报告${datePart}` }
  const samples = Array.isArray(report.details) ? report.details.length : 0
  const base =
    report.items_found != null
      ? `命中 ${report.items_found} 条候选 / 取回 ${samples} 篇样本${datePart}`
      : `取回 ${samples} 篇样本${datePart}`
  if (report.status === 'passed') return { tone: 'ok', text: `验收通过 · ${base}` }
  if (report.status === 'blocked') return { tone: 'block', text: `受阻 · 未绕过访问控制 · ${base}` }
  if (report.status === 'failed') return { tone: 'fail', text: `验收未通过 · ${base}` }
  return { tone: 'none', text: base }
}

// ---------- 行内二次确认（删除 / 强制启用，同一时刻最多一行） ----------

type ConfirmAction = 'delete' | 'force'
const confirming = ref<{ id: number; action: ConfirmAction } | null>(null)

function isConfirming(id: number | null | undefined, action: ConfirmAction): boolean {
  // 先落到局部变量再判空：`ref.value` 是属性访问，TS 不在 `a?.b === c` 里收窄它
  const current = confirming.value
  if (!current || id == null) return false
  return current.id === id && current.action === action
}

// ---------- 启用开关 / 删除 / 进入详情 ----------

/** 开关点击：已启用 → 停用；未启用已验收 → 启用；未验收 → 先弹强制启用确认。 */
async function toggleSource(item: SourceDTO) {
  if (item.id == null) return
  confirming.value = null
  if (item.status === 'active') {
    await disableSourceById(item.id, item.label)
  } else if (item.verified) {
    await enableSourceById(item.id, item.label)
  } else {
    confirming.value = { id: item.id, action: 'force' }
  }
}

async function confirmForce(item: SourceDTO) {
  if (item.id == null) return
  confirming.value = null
  await enableSourceById(item.id, item.label, true)
}

async function confirmDelete(item: SourceDTO) {
  if (item.id == null) return
  confirming.value = null
  await deleteSourceById(item.id, item.label)
}

function openDetail(item: SourceDTO) {
  if (item.id != null) router.push(`/sources/${item.id}`)
}

// ---------- 取值方式（engine）中文标签（仅展示；无头只有 browser_driven 一条路径，
// html_list_rendered 渲染取数已按 2026-10-21 裁定整体删除） ----------

const ENGINE_LABEL: Record<string, string> = {
  html_list: '静态取数',
  browser_driven: '无头浏览器',
}

function engineLabel(item: SourceDTO): string {
  return ENGINE_LABEL[item.engine] ?? item.engine
}
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
        <div class="source-info">
          <!-- 名称行 -->
          <div class="source-title-row">
            <p class="source-name" :title="item.label">{{ item.label }}</p>
            <span class="tag" :class="item.status === 'active' ? 'on' : item.status === 'disabled' ? 'off' : 'neutral'">
              {{ STATUS_LABEL[item.status] }}
            </span>
            <span v-if="item.builtin" class="tag neutral">内置</span>
            <span v-if="!item.builtin" class="tag" :class="item.verified ? 'verified' : 'pending'">
              {{ item.verified ? '已验收' : '待验收' }}
            </span>
            <span v-if="item.engine === 'browser_driven'" class="tag neutral">无头浏览器</span>
          </div>
          <!-- 元信息行 -->
          <p class="source-meta">
            <code class="key">{{ item.key }}</code>
            <span class="dot">·</span>
            <template v-if="item.entrypoints.length">
              入口 {{ item.entrypoints.join('、') }}
              <span class="dot">·</span>
            </template>
            取值方式
            <code class="key">{{ item.builtin ? 'mcp:gov_cn' : engineLabel(item) }}</code>
            <span class="dot">·</span>
            v{{ item.version }}
          </p>
          <!-- 摘要行 -->
          <p class="source-summary" :class="summaryOf(item).tone">
            <AppIcon
              :name="summaryOf(item).tone === 'ok'
                ? 'circle-check'
                : summaryOf(item).tone === 'fail'
                  ? 'circle-x'
                  : summaryOf(item).tone === 'block'
                    ? 'triangle-alert'
                    : 'clock'"
              :size="13"
              class="summary-icon"
            />
            <span>{{ summaryOf(item).text }}</span>
          </p>
          <p v-if="item.notes" class="source-notes">{{ item.notes }}</p>

          <!-- 二次确认（强制启用 / 删除）：落在卡片内部、操作列下方 -->
          <div v-if="item.id != null && confirming?.id === item.id" class="confirm-row">
            <template v-if="isConfirming(item.id, 'force')">
              <span class="confirm-text">未通过验收，确认强制启用？风险自负</span>
              <button class="mini-btn danger" type="button" :disabled="busySourceId === item.id" @click="confirmForce(item)">
                确认强制启用
              </button>
              <button class="mini-btn" type="button" @click="confirming = null">取消</button>
            </template>
            <template v-else>
              <span class="confirm-text">删除后不可恢复（已抓的任务与成果不受影响）</span>
              <button class="mini-btn danger" type="button" :disabled="busySourceId === item.id" @click="confirmDelete(item)">
                确认删除
              </button>
              <button class="mini-btn" type="button" @click="confirming = null">取消</button>
            </template>
          </div>
        </div>

        <div class="source-actions">
          <span v-if="item.id != null" class="switch-label">启用</span>
          <button
            v-if="item.id != null"
            class="switch"
            :class="{ on: item.status === 'active' }"
            type="button"
            role="switch"
            :aria-checked="item.status === 'active'"
            :disabled="busySourceId === item.id"
            :title="item.status === 'active'
              ? '停用：从抓取来源注册表摘掉（已抓的不受影响）'
              : item.verified
                ? '启用：纳入抓取来源'
                : '未通过验收：点这里确认后强制启用，或进详情重跑接入'"
            @click="toggleSource(item)"
          >
            <span class="knob" />
          </button>
          <button
            v-if="item.id != null"
            class="icon-btn danger"
            type="button"
            :disabled="busySourceId === item.id"
            title="删除来源"
            @click="confirming = { id: item.id, action: 'delete' }"
          >
            <AppIcon name="trash-2" :size="14" />
          </button>
          <button
            v-if="item.id != null"
            class="icon-btn go"
            type="button"
            title="进入详情：试运行 / 健康检查 / 验收报告"
            @click="openDetail(item)"
          >
            <AppIcon name="chevron-right" :size="16" />
          </button>
        </div>
      </li>
    </ul>

    <p class="panel-hint">
      只有「已启用」的来源才会出现在抓取配置的来源下拉里；内置来源随服务发布，不能修改。
      点卡片右侧的箭头进入详情，可试运行（真实抓取，进度在抓取页）、健康检查（只读第 1 页）与复核验收报告。
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

/* ---------- 卡片 ---------- */

.source-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.source {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
}

.source-info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.source-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.source-name {
  min-width: 0;
  font-size: 15px;
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
  padding: 2px 8px;
  background: #f1f2f4;
  border-radius: 6px;
}

.source-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: normal;
  color: var(--text-secondary);
}

.summary-icon {
  flex-shrink: 0;
  color: var(--text-tertiary);
}

.source-summary.ok .summary-icon {
  color: var(--green);
}

.source-summary.fail {
  color: var(--accent);
}

.source-summary.block {
  color: var(--amber);
}

.source-notes {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

/* ---------- 二次确认行 ---------- */

.confirm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
  padding: 8px 10px;
  background: var(--amber-bg);
  border: 1px solid #f0e0c8;
  border-radius: 8px;
}

.confirm-text {
  flex: 1 1 auto;
  min-width: 200px;
  font-size: 12px;
  color: var(--text-secondary);
}

/* ---------- 操作列 ---------- */

.source-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.switch-label {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.switch {
  position: relative;
  width: 40px;
  height: 22px;
  padding: 2px;
  border: none;
  border-radius: 999px;
  background: #d7dade;
  cursor: pointer;
  transition: background 0.15s ease;
}

.switch.on {
  background: var(--green);
}

.switch:disabled {
  opacity: 0.55;
  cursor: default;
}

.knob {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 1px 2px #00000026;
  transition: transform 0.15s ease;
}

.switch.on .knob {
  transform: translateX(18px);
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

.icon-btn.go {
  border-color: transparent;
  color: var(--text-tertiary);
}

.icon-btn.go:hover {
  color: var(--text-primary);
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

.mini-btn.danger {
  background: var(--red-bg);
  border-color: var(--accent);
  color: var(--accent);
}

@media (max-width: 720px) {
  .source {
    flex-direction: column;
    align-items: stretch;
  }

  .source-actions {
    justify-content: flex-end;
  }
}
</style>
