<script setup lang="ts">
/**
 * 验收报告（`dry_run_ingestion` 的产出，原样渲染，**不裁剪**）。
 *
 * 一份报告只有四个部分必须都在：状态徽章 / 判据表 / **未测试项** / **覆盖范围限制**。
 * 后两个是这次「没验证什么」——把它们折进「通过」里，报告就成了好消息合集，
 * 人也就没法据它决定要不要启用。`blocked` 与 `failed` 的文案也**必须分开**：
 * 前者是站点或环境不允许（我们没有绕过访问控制），后者是我们配置配错了。
 *
 * 列表页看历史验收记录与导入向导的最后一屏用的是同一个组件，口径不会漂。
 */
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { ImportErrorKind, SourceImportDetail, SourceImportReport } from '../api/types'

const props = defineProps<{
  report: SourceImportReport | null
  /** 作业的失败性质（报告里没有 status 时靠它判 blocked / failed） */
  errorKind?: ImportErrorKind | null
  error?: string | null
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
    hint: '试运行的真实抓取与正文校验都过了。启用后该来源才会进入抓取来源注册表。',
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

/** 硬判据没过 = 这份配置不能用；软判据只在页面上标出来，不判失败。 */
function toneOf(passed: boolean, hard: boolean): string {
  if (passed) return 'ok'
  return hard ? 'fail' : 'warn'
}
</script>

<template>
  <div class="report">
    <!-- 状态徽章 + 一句话说明 -->
    <div class="badge" :class="meta.tone">
      <AppIcon :name="meta.icon" :size="14" class="badge-icon" />
      <span class="badge-label">{{ meta.label }}</span>
      <span v-if="report?.fingerprint" class="badge-fp" :title="report.fingerprint">
        指纹 {{ String(report.fingerprint).slice(0, 8) }}
      </span>
    </div>
    <p class="badge-hint">{{ error || meta.hint }}</p>

    <p v-if="report?.items_found != null" class="stat">
      本次试抓命中 <b>{{ report.items_found }}</b> 条候选，取回 <b>{{ details.length }}</b> 篇正文样本
    </p>

    <!-- 判据表 -->
    <section v-if="criteria.length" class="block">
      <p class="block-title">验收判据</p>
      <ul class="criteria">
        <li
          v-for="(item, i) in criteria"
          :key="i"
          class="criterion"
          :class="toneOf(item.passed, item.hard)"
        >
          <AppIcon
            :name="item.passed ? 'circle-check' : item.hard ? 'circle-x' : 'info'"
            :size="13"
            class="criterion-icon"
          />
          <span class="criterion-name">{{ item.name }}</span>
          <span class="criterion-value">
            <span class="criterion-actual">{{ item.actual }}</span>
            <span class="criterion-threshold">要求 {{ item.threshold }}</span>
          </span>
          <span v-if="!item.hard" class="criterion-tag">软判据</span>
          <span v-if="item.note" class="criterion-note">{{ item.note }}</span>
        </li>
      </ul>
    </section>

    <!-- 逐篇正文实测 -->
    <section v-if="details.length" class="block">
      <p class="block-title">正文实测（逐篇）</p>
      <ul class="details">
        <li v-for="(item, i) in details" :key="i" class="detail">
          <div class="detail-head">
            <AppIcon
              :name="item.body_ok === false ? 'circle-x' : 'circle-check'"
              :size="12"
              class="detail-icon"
              :class="{ bad: item.body_ok === false }"
            />
            <span class="detail-title" :title="item.title || item.url || ''">
              {{ item.title || item.url || '（无标题）' }}
            </span>
            <a
              v-if="item.url"
              class="detail-link"
              :href="item.url"
              target="_blank"
              rel="noreferrer noopener"
              title="打开原文"
            >
              <AppIcon name="external-link" :size="12" />
            </a>
          </div>
          <p class="detail-metrics">
            <span v-if="item.body_chars != null">正文 {{ item.body_chars }} 字</span>
            <span v-if="item.body_cjk != null">中文 {{ item.body_cjk }} 字</span>
            <span v-if="item.sent_enders != null">句末 {{ item.sent_enders }}</span>
            <span v-if="item.image_count != null && item.image_count > 0">图片版（{{ item.image_count }} 张图）</span>
            <span v-if="item.date">{{ item.date }}</span>
            <span v-if="item.agency">{{ item.agency }}</span>
          </p>
          <p v-if="item.container_selector" class="detail-selector">
            正文容器 {{ item.container_selector }}
          </p>
          <ul v-if="item.warnings?.length" class="detail-warnings">
            <li v-for="(w, j) in item.warnings" :key="j">{{ w }}</li>
          </ul>
        </li>
      </ul>
    </section>
    <p v-else-if="report" class="block-title muted">没有取回正文样本</p>

    <!-- 这两块是「这次没验证什么」，必须看得见（只要有报告就一定列出来，哪怕是空的） -->
    <template v-if="report">
      <section class="block">
        <p class="block-title">本次未测试</p>
        <ul v-if="untested.length" class="bullets">
          <li v-for="(item, i) in untested" :key="i">{{ item }}</li>
        </ul>
        <p v-else class="muted">无（报告没有列出未测试项）</p>
      </section>

      <section class="block">
        <p class="block-title">覆盖范围限制</p>
        <ul v-if="coverageLimits.length" class="bullets">
          <li v-for="(item, i) in coverageLimits" :key="i">{{ item }}</li>
        </ul>
        <p v-else class="muted">无（报告没有列出覆盖范围限制）</p>
      </section>
    </template>

    <!-- 连试运行都没跑到（勘察/内容发现就受阻）：没有报告，上面那句 error 就是全部结论 -->
    <p v-else class="muted">这次作业没有产出验收报告。</p>
  </div>
</template>

<style scoped>
.report {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.badge.ok {
  background: var(--green-bg);
  color: var(--green);
}

.badge.fail {
  background: var(--red-bg);
  color: var(--accent);
}

.badge.block {
  background: #fff4e5;
  color: #b26a00;
}

.badge.wait {
  background: #f1f2f4;
  color: var(--text-secondary);
}

.badge-fp {
  padding-left: 6px;
  border-left: 1px solid currentColor;
  opacity: 0.75;
  font-weight: 500;
  font-family: var(--font-mono, ui-monospace, monospace);
}

.badge-icon {
  flex-shrink: 0;
}

.badge-hint {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.stat {
  font-size: 12px;
  color: var(--text-secondary);
}

.stat b {
  color: var(--text-primary);
  font-weight: 600;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 6px;
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

.bullets {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

/* ---------- 判据表 ---------- */

.criteria {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.criterion {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  padding: 7px 10px;
  background: #f7f8f9;
  border-left: 2px solid var(--divider);
  border-radius: 6px;
  font-size: 12px;
}

.criterion.ok {
  border-left-color: var(--green);
}

.criterion.fail {
  border-left-color: var(--accent);
}

.criterion.warn {
  border-left-color: #d9a13b;
}

.criterion-icon {
  flex-shrink: 0;
  align-self: center;
}

.criterion.ok .criterion-icon {
  color: var(--green);
}

.criterion.fail .criterion-icon {
  color: var(--accent);
}

.criterion.warn .criterion-icon {
  color: #b26a00;
}

.criterion-name {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--text-primary);
  font-weight: 500;
}

.criterion-value {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text-secondary);
}

.criterion-actual {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.criterion-threshold {
  flex-shrink: 0;
  color: var(--text-tertiary);
}

.criterion-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.criterion-note {
  flex: 1 1 100%;
  color: var(--text-tertiary);
}

/* ---------- 正文实测 ---------- */

.details {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  border: 1px solid var(--divider);
  border-radius: 8px;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 6px;
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
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-link {
  flex-shrink: 0;
  display: flex;
  color: var(--text-tertiary);
}

.detail-link:hover {
  color: var(--accent);
}

.detail-metrics,
.detail-selector {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.detail-selector {
  font-family: var(--font-mono, ui-monospace, monospace);
}

.detail-warnings {
  margin: 0;
  padding-left: 16px;
  font-size: 11px;
  color: #b26a00;
  line-height: 1.6;
}
</style>
