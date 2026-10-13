<script setup lang="ts">
/**
 * 定时入库弹窗（Phase 11）：遮罩层 + 居中卡片。
 *
 * 两个视图：
 * - list   现有定时任务列表（TopBar「定时任务」入口打开）：仅展示与启停 / 立即运行 / 删除，不提供新建入口
 * - create 用当前抓取配置快照新建定时任务（抓取配置选「定时自动入库」后打开，唯一的新建入口）
 *
 * 列表数据在 store.schedules；弹窗打开与视图切换由 store.openScheduleModal 驱动。
 */
import { computed, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import type { ScheduleJobDTO, ScheduleKind, LastRunStatus } from '../api/types'
import {
  buildSchedulePayload,
  closeScheduleModal,
  createSchedule,
  deleteScheduleJob,
  formatRunAt,
  form,
  runScheduleNow,
  scheduleModalOpen,
  scheduleModalView,
  schedules,
  schedulesLoading,
  toggleScheduleJob,
} from '../store'

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const KIND_OPTIONS: { kind: ScheduleKind; label: string }[] = [
  { kind: 'minutely', label: '每 N 分钟' },
  { kind: 'hourly', label: '每 N 小时' },
  { kind: 'daily', label: '每天' },
  { kind: 'weekly', label: '每周' },
]

const STATUS_LABEL: Record<LastRunStatus, string> = {
  completed: '成功',
  failed: '失败',
  noop: '无新增',
}

// ---------- 遮罩点击关窗 ----------

/**
 * 记录 mousedown 起点，click 时也要求不在卡片内才关窗。
 * 原先的 @click.self 拦不住「卡片内按住拖到遮罩上松开」（选中文本 / 拖动滚动）这种手势：
 * 按 DOM 规范 click 派发在 mousedown/mouseup 两目标的公共祖先上——此时公共祖先恰好是
 * overlay 自身，.self 判定反而通过，弹窗被误关；而在遮罩上单纯点击时 target 是 .mask，
 * .self 本就不通过（点遮罩关窗其实从未生效）。用「按下与抬起都不在卡片内」判定后，
 * 两种手势都得到正确结果。
 */
let downOutsideCard = false

function onOverlayDown(event: MouseEvent) {
  downOutsideCard = !(event.target as Element).closest('.card')
}

function onOverlayClick(event: MouseEvent) {
  if (downOutsideCard && !(event.target as Element).closest('.card')) closeScheduleModal()
}

// ---------- 列表视图 ----------

function scheduleText(job: ScheduleJobDTO): string {
  if (job.schedule_kind === 'minutely') return `每 ${job.interval_minutes} 分钟`
  if (job.schedule_kind === 'hourly') return `每 ${(job.interval_minutes ?? 60) / 60} 小时`
  if (job.schedule_kind === 'daily') return `每天 ${job.run_time}`
  return `每周${WEEKDAYS[job.run_weekday ?? 0]} ${job.run_time}`
}

/** 配置摘要一行（关键词 / 机构 / 文种 / 回溯起点），让用户不展开表单也知道抓什么 */
function configSummary(job: ScheduleJobDTO): string {
  const bits: string[] = []
  if (job.keywords.length) bits.push(`关键词「${job.keywords.join('、')}」`)
  if (job.agencies.length) bits.push(`机构 ${job.agencies.length} 项`)
  if (job.doc_types.length) bits.push(`文种 ${job.doc_types.join('、')}`)
  if (job.date_from) bits.push(`自 ${job.date_from} 起`)
  else bits.push('不限起始')
  return bits.join(' · ')
}

/** 删除是两步确认：先点垃圾桶变成「确认删除」，再点才真删 */
const confirmingDeleteId = ref<number | null>(null)

// ---------- 新建视图 ----------

const draft = reactive({
  name: '',
  kind: 'daily' as ScheduleKind,
  everyMinutes: 30,
  everyHours: 2,
  runTime: '09:00',
  weekday: 0,
})
const submitting = ref(false)

const isIntervalKind = computed(() => draft.kind === 'minutely' || draft.kind === 'hourly')
const isWeekly = computed(() => draft.kind === 'weekly')

/** 当前抓取配置的摘要（创建后即成快照，改表单不影响已建任务） */
const formSummary = computed(() => {
  const bits: string[] = []
  if (form.keywords.length) bits.push(`关键词「${form.keywords.join('、')}」`)
  if (form.agencies.length) bits.push(`机构 ${form.agencies.length} 项`)
  if (form.doc_types.length) bits.push(`文种 ${form.doc_types.join('、')}`)
  if (form.date_from || form.date_to) bits.push(`${form.date_from ?? '…'} ~ ${form.date_to ?? '…'}`)
  else bits.push('不限时间')
  return bits.join(' · ')
})

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  try {
    const ok = await createSchedule({
      ...buildSchedulePayload(),
      name: draft.name.trim() || null,
      schedule_kind: draft.kind,
      every: draft.kind === 'minutely' ? draft.everyMinutes : draft.kind === 'hourly' ? draft.everyHours : null,
      run_time: isIntervalKind.value ? null : draft.runTime,
      run_weekday: isWeekly.value ? draft.weekday : null,
    })
    if (ok) {
      draft.name = ''
      scheduleModalView.value = 'list'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="scheduleModalOpen" class="overlay" @mousedown="onOverlayDown" @click="onOverlayClick">
    <div class="mask"></div>
    <div class="card" role="dialog" aria-modal="true" aria-label="定时任务">
      <!-- ================= 列表视图 ================= -->
      <template v-if="scheduleModalView === 'list'">
        <header class="head">
          <div class="head-left">
            <AppIcon name="timer" :size="16" class="head-icon" />
            <h2 class="head-title">定时任务</h2>
            <span class="head-count">{{ schedules.length }}</span>
          </div>
          <button class="close" type="button" aria-label="关闭" @click="closeScheduleModal()">
            <AppIcon name="x" :size="16" />
          </button>
        </header>

        <div class="body">
          <p v-if="schedulesLoading && !schedules.length" class="empty">加载中…</p>
          <div v-else-if="!schedules.length" class="empty">
            <AppIcon name="inbox" :size="28" class="empty-icon" />
            <p class="empty-text">还没有定时任务</p>
            <p class="empty-hint">在抓取任务页把入库方式选为「定时自动入库」并提交，即可创建定时任务</p>
          </div>

          <ul v-else class="job-list">
            <li v-for="job in schedules" :key="job.id" class="job" :class="{ disabled: !job.enabled }">
              <div class="job-main">
                <div class="job-title-row">
                  <p class="job-name">{{ job.name }}</p>
                  <span class="job-status" :class="job.last_status ?? 'none'">
                    {{ job.last_status ? STATUS_LABEL[job.last_status] : '未运行' }}
                  </span>
                </div>
                <p class="job-schedule">
                  <AppIcon name="calendar" :size="12" class="job-schedule-icon" />
                  {{ scheduleText(job) }}
                  <span class="dot">·</span>
                  下次 {{ job.enabled ? formatRunAt(job.next_run_at) : '已停用' }}
                  <span class="dot">·</span>
                  已运行 {{ job.run_count }} 次
                </p>
                <p class="job-config">{{ configSummary(job) }}</p>
              </div>

              <div class="job-actions">
                <button
                  class="icon-btn"
                  type="button"
                  :title="'立即运行一轮（不等下一个触发点）'"
                  @click="runScheduleNow(job)"
                >
                  <AppIcon name="play" :size="14" />
                </button>
                <button
                  v-if="confirmingDeleteId !== job.id"
                  class="icon-btn danger"
                  type="button"
                  title="删除"
                  @click="confirmingDeleteId = job.id"
                >
                  <AppIcon name="trash-2" :size="14" />
                </button>
                <button
                  v-else
                  class="confirm-delete"
                  type="button"
                  @click="deleteScheduleJob(job); confirmingDeleteId = null"
                >
                  确认删除
                </button>
                <!-- 启停开关 -->
                <button
                  class="switch"
                  :class="{ on: job.enabled }"
                  type="button"
                  role="switch"
                  :aria-checked="job.enabled"
                  :title="job.enabled ? '停用（保留配置）' : '启用'"
                  @click="toggleScheduleJob(job)"
                >
                  <span class="knob"></span>
                </button>
              </div>
            </li>
          </ul>
        </div>

        <footer class="foot">
          <p class="foot-hint">触发时自动检索、去重并全流程入库；服务重启后调度自动恢复</p>
        </footer>
      </template>

      <!-- ================= 新建视图 ================= -->
      <template v-else>
        <header class="head">
          <div class="head-left">
            <button class="back" type="button" aria-label="返回列表" @click="scheduleModalView = 'list'">
              <AppIcon name="chevron-right" :size="16" style="transform: rotate(180deg)" />
            </button>
            <AppIcon name="timer" :size="16" class="head-icon" />
            <h2 class="head-title">新建定时任务</h2>
          </div>
          <button class="close" type="button" aria-label="关闭" @click="closeScheduleModal()">
            <AppIcon name="x" :size="16" />
          </button>
        </header>

        <div class="body">
          <!-- 抓取配置快照：来自当前表单，创建后不再联动 -->
          <div class="field">
            <p class="label">抓取配置（当前表单快照）</p>
            <p class="snapshot">{{ formSummary }}</p>
          </div>

          <div class="field">
            <p class="label">任务名（可选）</p>
            <input
              v-model="draft.name"
              class="input"
              type="text"
              maxlength="100"
              placeholder="留空自动生成"
            />
          </div>

          <div class="field">
            <p class="label">运行周期</p>
            <div class="chips-row">
              <button
                v-for="opt in KIND_OPTIONS"
                :key="opt.kind"
                type="button"
                class="chip"
                :class="{ active: draft.kind === opt.kind }"
                @click="draft.kind = opt.kind"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>

          <div class="field" v-if="draft.kind === 'minutely'">
            <p class="label">间隔（分钟）</p>
            <input v-model.number="draft.everyMinutes" class="input narrow" type="number" min="1" max="1440" />
          </div>
          <div class="field" v-else-if="draft.kind === 'hourly'">
            <p class="label">间隔（小时）</p>
            <input v-model.number="draft.everyHours" class="input narrow" type="number" min="1" max="168" />
          </div>
          <div class="field" v-else>
            <p class="label">运行时刻（服务器本地时间）</p>
            <input v-model="draft.runTime" class="input narrow" type="time" />
          </div>

          <div class="field" v-if="isWeekly">
            <p class="label">星期</p>
            <div class="chips-row">
              <button
                v-for="(label, i) in WEEKDAYS"
                :key="i"
                type="button"
                class="chip"
                :class="{ active: draft.weekday === i }"
                @click="draft.weekday = i"
              >
                {{ label }}
              </button>
            </div>
          </div>

          <div class="hint-row">
            <AppIcon name="info" :size="12" class="hint-icon" />
            <p class="hint-text">
              每轮自动跳过已入库的原文；抓满篇数上限后自动向更早的日期回填，抓空后回到最新追新发文
            </p>
          </div>
        </div>

        <footer class="foot">
          <button class="ghost" type="button" @click="scheduleModalView = 'list'">取消</button>
          <button class="primary" type="button" :disabled="submitting" @click="onSubmit">
            {{ submitting ? '创建中…' : '创建定时任务' }}
          </button>
        </footer>
      </template>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.mask {
  position: absolute;
  inset: 0;
  background: #1f232999;
}

.card {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 620px;
  max-height: min(720px, calc(100vh - 48px));
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(15, 23, 42, 0.2);
  overflow: hidden;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--divider);
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.head-icon {
  color: var(--primary);
  flex-shrink: 0;
}

.head-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.head-count {
  font-size: 12px;
  color: var(--text-tertiary);
  background: #f1f2f4;
  padding: 2px 8px;
  border-radius: 999px;
}

.back,
.close {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  background: none;
  border: none;
  border-radius: 6px;
  color: var(--text-tertiary);
  line-height: normal;
}

.back:hover,
.close:hover {
  background: #f1f2f4;
  color: var(--text-primary);
}

.body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--divider);
}

.foot-hint {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

.primary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 16px;
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
  opacity: 0.6;
  cursor: default;
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
}

/* ---------- 列表 ---------- */

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 36px 0;
  text-align: center;
}

.empty-icon {
  color: var(--text-tertiary);
  opacity: 0.6;
}

.empty-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.empty-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  max-width: 40ch;
}

.job-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.job {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.job.disabled {
  opacity: 0.6;
}

.job-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.job-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.job-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-status {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f2f4;
  color: var(--text-tertiary);
}

.job-status.completed {
  background: var(--green-bg);
  color: var(--green);
}

.job-status.failed {
  background: var(--red-bg);
  color: var(--accent);
}

.job-status.noop {
  background: var(--blue-bg);
  color: var(--blue);
}

.job-schedule {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
}

.job-schedule-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.dot {
  color: var(--text-tertiary);
}

.job-config {
  font-size: 12px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
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

.icon-btn.danger {
  color: var(--accent);
}

.icon-btn.danger:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.confirm-delete {
  padding: 6px 10px;
  background: var(--red-bg);
  border: 1px solid var(--accent);
  border-radius: 8px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
  white-space: nowrap;
}

/* 启停开关 */
.switch {
  position: relative;
  width: 38px;
  height: 22px;
  padding: 0;
  background: #d4d7dc;
  border: none;
  border-radius: 999px;
  transition: background 0.15s;
  flex-shrink: 0;
}

.switch.on {
  background: var(--accent);
}

.knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  background: #ffffff;
  border-radius: 50%;
  transition: transform 0.15s;
}

.switch.on .knob {
  transform: translateX(16px);
}

/* ---------- 新建表单 ---------- */

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.snapshot {
  padding: 10px 12px;
  background: #f7f8f9;
  border: 1px solid var(--divider);
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.input {
  padding: 9px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-primary);
}

.input:focus {
  outline: none;
  border-color: var(--accent);
}

.input.narrow {
  width: 160px;
}

.chips-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.chip {
  padding: 8px 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.chip.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 500;
  color: var(--accent);
}

.hint-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.hint-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
  margin-top: 2px;
}

.hint-text {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

@media (max-width: 640px) {
  .overlay {
    padding: 12px;
    align-items: flex-end;
  }

  .card {
    max-height: calc(100vh - 24px);
  }

  .job {
    flex-wrap: wrap;
  }
}
</style>
