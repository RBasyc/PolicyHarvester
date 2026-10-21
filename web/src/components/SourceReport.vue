<script setup lang="ts">
/**
 * 验收报告（`dry_run_ingestion` 的产出，原样渲染，**不裁剪**）。
 *
 * 版式对齐 design.pen「来源详情 · 试运行结果（优化版）」：
 *   验收结论卡（结论 + 四项统计 + 运行元信息）→ 验收判据卡（2 列 grid，**行内等高**——
 *   设计稿里判据卡高度随内容变化不齐，这里用 grid stretch 修掉）→ 正文实测卡（逐篇
 *   指标 chip + 容器 + 兜底提示）→ 底部双卡（未测试 / 覆盖范围限制）。
 *
 * 一份报告只有四个部分必须都在：状态徽章 / 判据表 / **未测试项** / **覆盖范围限制**。
 * 后两个是这次「没验证什么」——把它们折进「通过」里，报告就成了好消息合集，
 * 人也就没法据它决定要不要启用。`blocked` 与 `failed` 的文案也**必须分开**：
 * 前者是站点或环境不允许（我们没有绕过访问控制），后者是我们配置配错了。
 *
 * 列表页历史验收记录、导入向导的最后一屏与来源详情页用的是同一个组件，口径不会漂。
 */
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { ImportErrorKind, SourceImportDetail, SourceImportReport } from '../api/types'

const props = defineProps<{
  report: SourceImportReport | null
  /** 作业的失败性质（报告里没有 status 时靠它判 blocked / failed） */
  errorKind?: ImportErrorKind | null
  error?: string | null
  /** 这次试运行的完成时间（报告本身不带时间，由调用方从作业/接入记录传入） */
  finishedAt?: string | null
}>()

type Tone = 'ok' | 'fail' | 'block' | 'wait'

const status = computed<'passed' | 'failed' | 'blocked' | 'unknown'>(() => {
  const value = props.report?.status
  if (value === 'passed' || value === 'failed' || value === 'blocked') return value
  if (props.errorKind === 'blocked') return 'blocked'
  if (props.error) return 'failed'
  return 'unknown'
})

const STATUS_META: Record<string, { label: string; tone: Tone; icon: string; hint: string }> = {
  passed: {
    label: '验收通过',
    tone: 'ok',
    icon: 'circle-check',
    hint: '真实抓取与正文校验均已通过，启用后进入抓取来源注册表。',
  },
  failed: {
    label: '验收未通过',
    tone: 'fail',
    icon: 'circle-x',
    hint: '配置或站点结构与预期不符（我们没配对），不是访问被拒。下方判据里没过的项就是原因。',
  },
  blocked: {
    label: '受阻 · 未绕过访问控制',
    tone: 'block',
    icon: 'square',
    hint: '站点或当前环境不允许本次抓取（robots 限制 / 401 / 403 / 内网地址等）。按约定不绕过登录、验证码与付费限制，因此就此停住，配置未生效。',
  },
  unknown: {
    label: '暂无验收结论',
    tone: 'wait',
    icon: 'info',
    hint: '这次没有跑出可判定的试运行结果。',
  },
}

const meta = computed(() => STATUS_META[status.value])
const criteria = computed(() => props.report?.criteria ?? [])
const details = computed<SourceImportDetail[]>(() => props.report?.details ?? [])
const untested = computed(() => props.report?.untested ?? [])
const coverageLimits = computed(() => props.report?.coverage_limits ?? [])

const passedCount = computed(() => criteria.value.filter((c) => c.passed).length)
const warnCount = computed(() => criteria.value.filter((c) => !c.passed && !c.hard).length)
const failCount = computed(() => criteria.value.filter((c) => !c.passed && c.hard).length)

/** 指纹取前 8 位（报告卡里运行元信息那一行）。 */
const fingerprint = computed(() => String(props.report?.fingerprint ?? '').slice(0, 8))

function formatRunTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const runTime = computed(() => formatRunTime(props.finishedAt))

/** 指标 chip 的拼装：值缺失的项不进列表（模板里循环用）。 */
function metricChips(item: SourceImportDetail): { label: string; value: string }[] {
  const chips: { label: string; value: string }[] = []
  if (item.body_chars != null) chips.push({ label: '正文', value: `${item.body_chars} 字` })
  if (item.body_cjk != null) chips.push({ label: '中文', value: `${item.body_cjk} 字` })
  if (item.sent_enders != null) chips.push({ label: '句末', value: String(item.sent_enders) })
  if (item.date) chips.push({ label: '日期', value: item.date })
  if (item.agency) chips.push({ label: '机构', value: item.agency })
  if (item.doc_no) chips.push({ label: '文号', value: item.doc_no })
  if (item.image_count != null && item.image_count > 0) chips.push({ label: '图片版', value: `${item.image_count} 张图` })
  if (Array.isArray(item.attachments) && item.attachments.length > 0) chips.push({ label: '附件', value: `${item.attachments.length} 个` })
  return chips
}
</script>

<template>
  <div class="report">
    <!-- 验收结论卡 -->
    <section class="verdict" :class="meta.tone">
      <div class="verdict-icon-base">
        <AppIcon :name="meta.icon" :size="22" class="verdict-icon" />
      </div>
      <div class="verdict-text">
        <p class="verdict-label">{{ meta.label }}</p>
        <p class="verdict-hint">{{ error || meta.hint }}</p>
      </div>

      <template v-if="report">
        <div class="verdict-stats">
          <div v-if="report.items_found != null" class="verdict-stat">
            <p class="verdict-value">
              {{ report.items_found }}<span class="verdict-unit">条</span>
            </p>
            <p class="verdict-stat-label">命中候选</p>
          </div>
          <div class="verdict-stat">
            <p class="verdict-value">{{ details.length }}<span class="verdict-unit">篇</span></p>
            <p class="verdict-stat-label">正文样本</p>
          </div>
          <div v-if="criteria.length" class="verdict-stat">
            <p class="verdict-value">
              {{ passedCount }}/{{ criteria.length }}
            </p>
            <p class="verdict-stat-label">判据通过</p>
          </div>
          <div v-if="criteria.length" class="verdict-stat">
            <p class="verdict-value">{{ warnCount }}<span class="verdict-unit">项</span></p>
            <p class="verdict-stat-label">软判据警告</p>
          </div>
        </div>

        <div class="verdict-meta">
          <p v-if="runTime" class="verdict-meta-line">
            <AppIcon name="clock" :size="12" />
            <span>{{ runTime }}</span>
          </p>
          <p v-if="fingerprint" class="verdict-meta-line mono" :title="report.fingerprint">
            <span class="verdict-hash">#</span>
            <span>{{ fingerprint }}</span>
          </p>
        </div>
      </template>
    </section>

    <!-- 判据表 -->
    <section v-if="criteria.length" class="card">
      <header class="card-head">
        <div class="card-head-left">
          <h3 class="card-title">验收判据</h3>
          <span class="chip-count">{{ criteria.length }} 项</span>
        </div>
        <div class="legend">
          <span class="legend-item ok">
            <AppIcon name="circle-check" :size="13" />
            <span>通过 {{ passedCount }}</span>
          </span>
          <span v-if="warnCount" class="legend-item warn">
            <AppIcon name="triangle-alert" :size="13" />
            <span>警告 {{ warnCount }}</span>
          </span>
          <span v-if="failCount" class="legend-item fail">
            <AppIcon name="circle-x" :size="13" />
            <span>未过 {{ failCount }}</span>
          </span>
        </div>
      </header>
      <!-- grid 行内等高：设计稿里判据卡高度随内容变化不齐，stretch 对齐修掉 -->
      <div class="criteria-grid">
        <article
          v-for="(item, i) in criteria"
          :key="i"
          class="criterion"
          :class="item.passed ? 'ok' : item.hard ? 'fail' : 'warn'"
        >
          <div class="criterion-head">
            <AppIcon
              :name="item.passed ? 'circle-check' : item.hard ? 'circle-x' : 'triangle-alert'"
              :size="16"
              class="criterion-icon"
            />
            <p class="criterion-name">{{ item.name }}</p>
            <span class="criterion-actual">{{ item.actual }}</span>
            <span v-if="!item.hard" class="soft-tag">软判据</span>
          </div>
          <p class="criterion-threshold">要求：{{ item.threshold }}</p>
          <p v-if="item.note" class="criterion-note">{{ item.note }}</p>
        </article>
      </div>
    </section>

    <!-- 逐篇正文实测 -->
    <section v-if="details.length" class="card">
      <header class="card-head">
        <div class="card-head-left">
          <h3 class="card-title">正文实测</h3>
          <span class="chip-count">逐篇 · {{ details.length }} 篇</span>
        </div>
        <p class="card-note">抽样页面的正文抓取与容器命中情况</p>
      </header>
      <ul class="details">
        <li v-for="(item, i) in details" :key="i" class="detail">
          <div class="detail-head">
            <AppIcon
              :name="item.body_ok === false ? 'circle-x' : 'circle-check'"
              :size="16"
              class="detail-icon"
              :class="{ bad: item.body_ok === false }"
            />
            <p v-if="item.title" class="detail-title" :title="item.title">{{ item.title }}</p>
            <p v-else class="detail-title url">
              <span class="detail-kind">PDF</span>
              <span :title="item.url || ''">{{ item.url || '（无标题）' }}</span>
            </p>
            <a
              v-if="item.url"
              class="detail-link"
              :href="item.url"
              target="_blank"
              rel="noreferrer noopener"
              title="打开原文"
            >
              <AppIcon name="external-link" :size="14" />
            </a>
          </div>
          <div v-if="metricChips(item).length || item.container_selector" class="detail-chips">
            <span v-for="(chip, j) in metricChips(item)" :key="j" class="metric-chip">
              <span class="metric-label">{{ chip.label }}</span>
              <span class="metric-value">{{ chip.value }}</span>
            </span>
            <span v-if="item.container_selector" class="metric-chip">
              <span class="metric-label">容器</span>
              <span class="metric-value mono">{{ item.container_selector }}</span>
            </span>
          </div>
          <p v-for="(w, j) in item.warnings ?? []" :key="`w-${j}`" class="detail-warning">
            <AppIcon name="triangle-alert" :size="13" />
            <span>{{ w }}</span>
          </p>
        </li>
      </ul>
    </section>
    <p v-else-if="report" class="block-title muted">没有取回正文样本</p>

    <!-- 这两块是「这次没验证什么」，必须看得见（只要有报告就一定列出来，哪怕是空的） -->
    <template v-if="report">
      <div class="bottom-grid">
        <section class="card">
          <header class="card-head">
            <div class="card-head-left">
              <h3 class="card-title">本次未测试</h3>
              <span class="chip-count">{{ untested.length }} 项</span>
            </div>
          </header>
          <ul v-if="untested.length" class="flag-list">
            <li v-for="(item, i) in untested" :key="i">
              <AppIcon name="circle-minus" :size="14" class="flag-icon" />
              <span>{{ item }}</span>
            </li>
          </ul>
          <p v-else class="muted">无（报告没有列出未测试项）</p>
        </section>

        <section class="card">
          <header class="card-head">
            <div class="card-head-left">
              <h3 class="card-title">覆盖范围限制</h3>
              <span class="chip-count">{{ coverageLimits.length }} 项</span>
            </div>
            <p class="card-note">以下范围本轮试运行未覆盖</p>
          </header>
          <ul v-if="coverageLimits.length" class="flag-list">
            <li v-for="(item, i) in coverageLimits" :key="i">
              <span class="flag-dot" />
              <span>{{ item }}</span>
            </li>
          </ul>
          <p v-else class="muted">无（报告没有列出覆盖范围限制）</p>
        </section>
      </div>
    </template>

    <!-- 连试运行都没跑到（勘察/内容发现就受阻）：没有报告，结论卡那句 error 就是全部结论 -->
    <p v-else class="muted">这次作业没有产出验收报告。</p>
  </div>
</template>

<style scoped>
.report {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ---------- 验收结论卡 ---------- */

.verdict {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 20px 24px;
  border: 1px solid var(--divider);
  border-radius: 12px;
  background: #f7f8f9;
}

.verdict.ok {
  background: var(--green-soft);
  border-color: var(--green-border);
}

.verdict.fail {
  background: var(--red-bg);
  border-color: #f2d3d7;
}

.verdict.block {
  background: var(--amber-bg);
  border-color: #f0e0c8;
}

.verdict-icon-base {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 999px;
  background: #ffffffb3;
  color: var(--text-tertiary);
}

.verdict.ok .verdict-icon-base {
  background: var(--green-bg);
  color: var(--green);
}

.verdict.fail .verdict-icon-base {
  background: #f6dfe2;
  color: var(--accent);
}

.verdict.block .verdict-icon-base {
  background: var(--amber-soft);
  color: var(--amber);
}

.verdict-text {
  flex: 1 1 280px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.verdict-label {
  font-size: 18px;
  font-weight: 600;
  line-height: normal;
  color: var(--text-primary);
}

.verdict.ok .verdict-label {
  color: var(--green);
}

.verdict.fail .verdict-label {
  color: var(--accent);
}

.verdict.block .verdict-label {
  color: var(--amber);
}

.verdict-hint {
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.verdict-stats {
  display: flex;
  align-items: center;
  gap: 20px;
}

.verdict-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.verdict-stat + .verdict-stat {
  padding-left: 20px;
  border-left: 1px solid var(--divider);
}

.verdict-value {
  font-size: 20px;
  font-weight: 600;
  line-height: normal;
  color: var(--text-primary);
}

.verdict-unit {
  margin-left: 2px;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
}

.verdict-stat-label {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.verdict-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-left: auto;
}

.verdict-meta-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.verdict-hash {
  font-weight: 600;
}

.verdict-meta-line.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}

/* ---------- 通用卡片 ---------- */

.card {
  display: flex;
  flex-direction: column;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 14px;
}

.card-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: normal;
}

.chip-count {
  padding: 4px 10px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.card-note {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: normal;
}

.block-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: normal;
}

.muted {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
}

/* ---------- 判据网格（行内等高） ---------- */

.legend {
  display: flex;
  align-items: center;
  gap: 14px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: normal;
}

.legend-item.ok {
  color: var(--green);
}

.legend-item.warn {
  color: var(--amber);
}

.legend-item.fail {
  color: var(--accent);
}

.criteria-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  padding: 2px 20px 20px;
}

.criterion {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  background: #f8f9fa;
  border-left: 3px solid var(--divider);
  border-radius: 8px;
}

.criterion.ok {
  border-left-color: #00000000;
}

.criterion.warn {
  background: var(--amber-bg);
  border-left-color: var(--amber);
}

.criterion.fail {
  background: var(--red-bg);
  border-left-color: var(--accent);
}

.criterion-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.criterion-icon {
  flex-shrink: 0;
}

.criterion.ok .criterion-icon {
  color: var(--green);
}

.criterion.warn .criterion-icon {
  color: var(--amber);
}

.criterion.fail .criterion-icon {
  color: var(--accent);
}

.criterion-name {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: normal;
}

.criterion-actual {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.soft-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  background: var(--amber-soft);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  color: var(--amber);
  line-height: normal;
}

.criterion-threshold {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-tertiary);
}

.criterion-note {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-tertiary);
}

.criterion.warn .criterion-note {
  color: var(--amber);
}

/* ---------- 正文实测 ---------- */

.details {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--divider);
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.detail-icon {
  flex-shrink: 0;
  color: var(--green);
}

.detail-icon.bad {
  color: var(--accent);
}

.detail-title {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-title.url {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-kind {
  flex-shrink: 0;
  padding: 1px 6px;
  background: #f1f2f4;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.detail-link {
  flex-shrink: 0;
  display: flex;
  color: var(--text-tertiary);
}

.detail-link:hover {
  color: var(--accent);
}

.detail-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.metric-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: #f1f2f4;
  border-radius: 6px;
  font-size: 11px;
  line-height: normal;
}

.metric-label {
  color: var(--text-tertiary);
}

.metric-value {
  color: var(--text-secondary);
}

.metric-value.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}

.detail-warning {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--amber);
}

.detail-warning svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* ---------- 底部双卡 ---------- */

.bottom-grid {
  display: grid;
  grid-template-columns: 5fr 7fr;
  gap: 12px;
}

.flag-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 2px 20px 20px;
  list-style: none;
}

.flag-list li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.flag-icon {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--text-tertiary);
}

.flag-dot {
  flex-shrink: 0;
  width: 5px;
  height: 5px;
  margin: 8px 4px 0;
  border-radius: 999px;
  background: var(--text-tertiary);
}

.bottom-grid .muted {
  padding: 2px 20px 20px;
}

@media (max-width: 900px) {
  .criteria-grid {
    grid-template-columns: 1fr;
  }

  .bottom-grid {
    grid-template-columns: 1fr;
  }

  .verdict-stats {
    flex-wrap: wrap;
  }
}
</style>
