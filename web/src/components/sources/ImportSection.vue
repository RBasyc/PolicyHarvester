<script setup lang="ts">
/**
 * 导入接入（Phase 16 自 SourceModal 平移 + 批量新增）：
 *   单条导入 —— 输入 → 进行中（SSE）→ 验收报告 → 候选切换重跑 / 启用该来源；
 *   批量导入 —— 粘贴 N 个地址排队逐个跑，聚合 SSE 推队列进度，行展开看报告。
 *
 * 状态全部在 `store/sourceImports.ts`（模块级）：切 tab / 组件重挂载都不丢；
 * 路由离开再回来由 `syncImportsOnMount` 重订阅对表。
 */
import { computed, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import SourceReport from '../SourceReport.vue'
import type { ImportJobStatus, SourceBatchChild } from '../../api/types'
import {
  batch,
  cancelBatchJob,
  cancelWizardImport,
  canEnableRegistered,
  canRerunCandidate,
  clock,
  enableRegistered,
  enableSourceById,
  expandBatchJob,
  LOG_LEVEL_TONE,
  registeredId,
  registeredStatus,
  reportPassed,
  requestUrl,
  rerunSelectedCandidate,
  resetBatch,
  resetWizard,
  startBatchImport,
  startWizardImport,
  STATUS_LABEL,
  wizard,
} from '../../store/sourceImports'

const emit = defineEmits<{ switchTab: [tab: 'list'] }>()

type Mode = 'single' | 'batch'
const mode = ref<Mode>('single')

// ---------- 批量队列展示 ----------

const JOB_STATUS_LABEL: Record<ImportJobStatus, string> = {
  queued: '排队中',
  running: '运行中',
  done: '完成',
  error: '未通过',
  cancelled: '已取消',
}

const JOB_STATUS_TONE: Record<ImportJobStatus, string> = {
  queued: 'wait',
  running: 'run',
  done: 'ok',
  error: 'fail',
  cancelled: 'off',
}

const TERMINAL: ImportJobStatus[] = ['done', 'error', 'cancelled']

const queue = computed<SourceBatchChild[]>(() => batch.snapshot?.queue ?? [])

const batchSummary = computed(() => {
  const children = queue.value
  return {
    total: children.length,
    running: children.filter((c) => c.status === 'running').length,
    queued: children.filter((c) => c.status === 'queued').length,
    passed: children.filter((c) => c.status === 'done').length,
    failed: children.filter((c) => c.status === 'error').length,
    blocked: children.filter((c) => c.error_kind === 'blocked').length,
    allDone: children.length > 0 && children.every((c) => TERMINAL.includes(c.status) || c.gone),
  }
})

function childSubtitle(child: SourceBatchChild): string {
  if (child.gone) return child.phase_text
  if (child.status === 'queued') return `排队等待 · 第 ${child.position} 位`
  return child.phase_text
}

/** 展开中的子作业报告：完成后从快照里拿（含候选与登记结果） */
const expandedReport = computed(() => batch.expandedJob?.report ?? null)
const expandedRegistered = computed(() => {
  const value = batch.expandedJob?.registered?.source_id
  return typeof value === 'number' ? value : null
})
const expandedPassed = computed(() => batch.expandedJob?.report?.status === 'passed')

async function enableExpanded() {
  if (expandedRegistered.value == null) return
  const label =
    typeof batch.expandedJob?.registered?.label === 'string'
      ? batch.expandedJob.registered.label
      : '该来源'
  await enableSourceById(expandedRegistered.value, label)
}
</script>

<template>
  <div class="import-wrap">
    <div class="sub-tabs" role="tablist" aria-label="导入方式">
      <button
        class="sub-tab"
        :class="{ active: mode === 'single' }"
        type="button"
        role="tab"
        :aria-selected="mode === 'single'"
        @click="mode = 'single'"
      >
        <AppIcon name="upload" :size="13" />
        <span>单条导入</span>
      </button>
      <button
        class="sub-tab"
        :class="{ active: mode === 'batch' }"
        type="button"
        role="tab"
        :aria-selected="mode === 'batch'"
        @click="mode = 'batch'"
      >
        <AppIcon name="layers" :size="13" />
        <span>批量导入</span>
      </button>
    </div>

    <!-- ================= 单条导入 ================= -->
    <template v-if="mode === 'single'">
      <section class="panel">
        <!-- ---------- 1. 输入 ---------- -->
        <template v-if="wizard.step === 'form'">
          <div class="field">
            <p class="label">栏目 / 入口页地址</p>
            <input
              v-model="wizard.draft.url"
              class="input"
              type="url"
              placeholder="https://www.example.gov.cn/zhengce/gongwen/"
              @keydown.enter.prevent="startWizardImport"
            />
            <p class="field-hint">
              给列出政策条目的那一页（栏目页）最准；给门户首页也能跑，只是要多往下找一层。
              只访问公网 http/https 地址，内网与云元数据地址会被拒绝。
            </p>
          </div>

          <div class="field">
            <p class="label">采集目标（可选）</p>
            <input
              v-model="wizard.draft.target"
              class="input"
              type="text"
              maxlength="200"
              placeholder="例如：省级水利政策文件；会写进勘察报告，便于事后核对"
            />
          </div>

          <div class="field">
            <p class="label">试抓关键词（可选，逗号分隔）</p>
            <input v-model="wizard.draft.keywords" class="input" type="text" placeholder="留空 = 不按关键词过滤" />
          </div>

          <div class="form-row">
            <div class="field">
              <p class="label">试抓篇数</p>
              <input v-model.number="wizard.draft.limit" class="input narrow" type="number" min="1" max="10" />
            </div>
            <div class="field">
              <p class="label">每个入口试抓页数</p>
              <input v-model.number="wizard.draft.pages" class="input narrow" type="number" min="1" max="5" />
            </div>
          </div>

          <div class="hint-row">
            <AppIcon name="info" :size="12" class="hint-icon" />
            <p class="hint-text">
              接入分四步：勘察入口页 → 发现内容入口 → 生成配置 → <b>试运行验收</b>。
              试运行是<b>真实联网</b>抓若干篇并校验正文，因此会慢一些（几十秒）；
              全程遵守站点的 robots 与访问频率限制，不绕过登录、验证码或付费限制。
            </p>
          </div>

          <p v-if="wizard.formError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ wizard.formError }}</span>
          </p>

          <div class="foot">
            <p class="foot-hint">接入过程不经过大模型：配置由勘察报告里得分最高的候选组装</p>
            <button class="primary" type="button" :disabled="wizard.starting" @click="startWizardImport">
              {{ wizard.starting ? '提交中…' : '开始接入' }}
            </button>
          </div>
        </template>

        <!-- ---------- 2. 进行中 ---------- -->
        <template v-else-if="wizard.step === 'running'">
          <div class="phase">
            <AppIcon name="loader-circle" :size="14" class="phase-icon spin" />
            <span class="phase-text">{{ wizard.job?.phase_text ?? '提交作业…' }}</span>
            <span class="phase-url">{{ wizard.job?.url ?? requestUrl() }}</span>
          </div>

          <div class="log-box">
            <p v-if="!wizard.jobLogs.length" class="log-empty">等待日志…</p>
            <div v-for="line in wizard.jobLogs" :key="line.seq" class="log-line">
              <span class="log-time">{{ clock(line.created_at) }}</span>
              <span class="log-msg" :class="LOG_LEVEL_TONE[line.level] ?? 'info'">
                {{ line.message }}
              </span>
            </div>
          </div>

          <p v-if="wizard.streamError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ wizard.streamError }}</span>
          </p>

          <div v-else class="hint-row">
            <AppIcon name="info" :size="12" class="hint-icon" />
            <p class="hint-text">
              试运行阶段没有更细的进度（正在真实联网抓取）——中途取消不会留下未验证的配置，
              已抓到的证据仍保留在「接入记录」里。
            </p>
          </div>

          <div class="foot">
            <p class="foot-hint">试运行是真实联网抓取，请勿重复提交（同一时间最多 3 个作业）</p>
            <button class="ghost" type="button" @click="cancelWizardImport">取消作业</button>
          </div>
        </template>

        <!-- ---------- 3. 报告 ---------- -->
        <template v-else>
          <SourceReport
            :report="wizard.job?.report ?? null"
            :error-kind="wizard.job?.error_kind ?? null"
            :error="wizard.job?.error ?? null"
          />

          <!-- 候选切换：自动用的是 selected 那条；选中另一个，再用底部「用选中候选重跑」 -->
          <section v-if="wizard.job?.candidates.length" class="block">
            <p class="block-title">列表栏目候选（选中一个，再用底部按钮重跑试运行）</p>
            <ul class="candidates">
              <li v-for="candidate in wizard.job.candidates" :key="candidate.index">
                <button
                  class="candidate"
                  :class="{
                    selected: wizard.selectedCandidateIndex === candidate.index,
                    rejected: !!candidate.rejected_reason,
                  }"
                  type="button"
                  :title="`${candidate.page_url} · ${candidate.list_selector}`"
                  @click="wizard.selectedCandidateIndex = candidate.index"
                >
                  <span class="candidate-name">
                    {{ candidate.name }}
                    <span v-if="candidate.selected" class="candidate-flag">当前使用</span>
                    <span v-else-if="candidate.rejected_reason" class="candidate-flag bad">已试过 · 不行</span>
                    <span v-else-if="wizard.selectedCandidateIndex === candidate.index" class="candidate-flag">
                      已选中
                    </span>
                  </span>
                  <span class="candidate-meta">
                    {{ candidate.item_count ?? '?' }} 条 · <code>{{ candidate.list_selector }}</code>
                  </span>
                  <span v-if="candidate.sample_title" class="candidate-sample">
                    样本：{{ candidate.sample_title }}
                  </span>
                  <!-- 「没试」和「试了不行」要分得清：逐个候选真抓过一篇样本详情页，
                       失败的原因原样写在这里，而不是笼统说一句「导入失败」。 -->
                  <span v-if="candidate.rejected_reason" class="candidate-reason">
                    {{ candidate.rejected_reason }}
                  </span>
                </button>
              </li>
            </ul>
          </section>

          <p v-if="wizard.streamError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ wizard.streamError }}</span>
          </p>

          <div class="foot">
            <p class="foot-hint">
              <template v-if="registeredStatus">
                已登记为 <b>{{ STATUS_LABEL[registeredStatus] }}</b>
                ：通过验收后需人工点「启用」才会进入抓取来源
              </template>
              <template v-else-if="wizard.job?.status === 'cancelled'">作业已取消，未登记任何来源</template>
              <template v-else>这次没有登记新配置（失败不覆盖已在跑的来源）</template>
            </p>
            <button class="ghost" type="button" @click="emit('switchTab', 'list')">回到列表</button>
            <button class="ghost" type="button" @click="resetWizard">重新接入</button>
            <button
              v-if="canRerunCandidate"
              class="ghost"
              type="button"
              :disabled="wizard.starting"
              title="沿用当前配置骨架，只替换列表入口与正文规则，再跑一次真实联网试运行"
              @click="rerunSelectedCandidate"
            >
              {{ wizard.starting ? '提交中…' : '用选中候选重跑' }}
            </button>
            <button
              v-if="registeredId != null && registeredStatus !== 'active'"
              class="primary"
              type="button"
              :disabled="!canEnableRegistered"
              :title="
                reportPassed
                  ? '纳入抓取来源（有通过验收）'
                  : '这份配置没有通过验收，先换候选重跑或回到来源列表用「强制启用」'
              "
              @click="enableRegistered"
            >
              启用该来源
            </button>
          </div>
        </template>
      </section>
    </template>

    <!-- ================= 批量导入 ================= -->
    <template v-else>
      <section class="panel">
        <template v-if="!batch.snapshot">
          <div class="field">
            <p class="label">栏目 / 入口页地址（每行一个，最多 20 个）</p>
            <textarea
              v-model="batch.draft.urls"
              class="input urls-box"
              rows="6"
              placeholder="https://www.example.gov.cn/zhengce/gongwen/&#10;https://www.another.gov.cn/xxgk/&#10;# 以 # 开头的行会被忽略"
            ></textarea>
            <p class="field-hint">
              地址会<strong>排队逐个接入</strong>（同时最多跑 3 个，其余排队等待）；
              每个地址都走与单条导入相同的勘察 → 配置 → 试运行验收全流程，
              验收通过的来源落为<strong>草案</strong>，需到「来源列表」人工启用。
            </p>
          </div>

          <div class="field">
            <p class="label">采集目标（可选，写进每份勘察报告）</p>
            <input v-model="batch.draft.target" class="input" type="text" maxlength="200" />
          </div>

          <div class="field">
            <p class="label">试抓关键词（可选，逗号分隔）</p>
            <input v-model="batch.draft.keywords" class="input" type="text" placeholder="留空 = 不按关键词过滤" />
          </div>

          <div class="form-row">
            <div class="field">
              <p class="label">试抓篇数</p>
              <input v-model.number="batch.draft.limit" class="input narrow" type="number" min="1" max="10" />
            </div>
            <div class="field">
              <p class="label">每个入口试抓页数</p>
              <input v-model.number="batch.draft.pages" class="input narrow" type="number" min="1" max="5" />
            </div>
          </div>

          <p v-if="batch.formError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ batch.formError }}</span>
          </p>

          <div class="foot">
            <p class="foot-hint">批量导入同样不经过大模型；失败的行会给原因，不影响其他行</p>
            <button class="primary" type="button" :disabled="batch.starting" @click="startBatchImport">
              {{ batch.starting ? '提交中…' : '开始批量接入' }}
            </button>
          </div>
        </template>

        <!-- 批量进度队列 -->
        <template v-else>
          <div class="phase">
            <AppIcon
              name="loader-circle"
              :size="14"
              class="phase-icon"
              :class="{ spin: batchSummary.running > 0 || batchSummary.queued > 0 }"
            />
            <span class="phase-text">
              批量导入 · {{ batchSummary.total }} 个地址
              <template v-if="batchSummary.queued"> · 排队中 {{ batchSummary.queued }}</template>
              <template v-if="batchSummary.running"> · 运行中 {{ batchSummary.running }}</template>
              <template v-if="batchSummary.allDone">
                · 完成 {{ batchSummary.passed }} / 未通过 {{ batchSummary.failed }}
              </template>
            </span>
          </div>

          <p v-if="batch.streamError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ batch.streamError }}</span>
          </p>

          <ul class="queue">
            <li v-for="(child, idx) in queue" :key="child.job_id" class="queue-row">
              <div class="queue-main">
                <span class="queue-index">{{ idx + 1 }}</span>
                <span class="tag" :class="JOB_STATUS_TONE[child.status]">
                  {{ JOB_STATUS_LABEL[child.status] }}
                </span>
                <div class="queue-info">
                  <p class="queue-url" :title="child.url">{{ child.url }}</p>
                  <p class="queue-sub">{{ childSubtitle(child) }}</p>
                  <p v-if="child.error" class="queue-error">{{ child.error }}</p>
                  <p v-else-if="batch.lastLogs[child.job_id]" class="queue-lastlog">
                    {{ clock(batch.lastLogs[child.job_id].created_at) }}
                    {{ batch.lastLogs[child.job_id].message }}
                  </p>
                </div>
                <div class="queue-actions">
                  <button
                    v-if="child.status === 'queued' || child.status === 'running'"
                    class="mini-btn"
                    type="button"
                    @click="cancelBatchJob(child.job_id)"
                  >
                    取消
                  </button>
                  <button
                    class="mini-btn"
                    type="button"
                    :class="{ on: batch.expandedJobId === child.job_id }"
                    @click="expandBatchJob(batch.expandedJobId === child.job_id ? null : child.job_id)"
                  >
                    {{ batch.expandedJobId === child.job_id ? '收起' : '详情' }}
                  </button>
                </div>
              </div>

              <!-- 行展开：日志（在跑）+ 验收报告（完成） -->
              <div v-if="batch.expandedJobId === child.job_id" class="queue-detail">
                <template v-if="batch.expandedJob">
                  <div v-if="batch.expandedJob.status === 'running' || batch.expandedJob.status === 'queued'" class="log-box">
                    <p v-if="!batch.expandedLogs.length" class="log-empty">等待日志…</p>
                    <div v-for="line in batch.expandedLogs" :key="line.seq" class="log-line">
                      <span class="log-time">{{ clock(line.created_at) }}</span>
                      <span class="log-msg" :class="LOG_LEVEL_TONE[line.level] ?? 'info'">
                        {{ line.message }}
                      </span>
                    </div>
                  </div>

                  <SourceReport
                    :report="expandedReport"
                    :error-kind="batch.expandedJob.error_kind ?? null"
                    :error="batch.expandedJob.error ?? null"
                  />

                  <div v-if="expandedRegistered != null" class="foot">
                    <p class="foot-hint">验收通过的来源已登记为草案，启用后才进入抓取来源</p>
                    <button
                      v-if="expandedPassed"
                      class="primary"
                      type="button"
                      @click="enableExpanded"
                    >
                      启用该来源
                    </button>
                  </div>
                </template>
                <p v-else class="empty">读取作业详情…</p>
              </div>
            </li>
          </ul>

          <div class="foot">
            <p class="foot-hint">
              实时进度保存在服务进程内（保留约 30 分钟）；每次勘察/试运行的完整报告持久保留在「接入记录」里。
            </p>
            <button v-if="batchSummary.allDone" class="ghost" type="button" @click="resetBatch">
              清空列表 / 再来一批
            </button>
          </div>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.import-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sub-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: #eff0f3;
  border-radius: 9px;
  align-self: flex-start;
}

.sub-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
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

.sub-tab:hover {
  color: var(--text-primary);
}

.sub-tab.active {
  background: var(--card);
  font-weight: 600;
  color: var(--text-primary);
  box-shadow: 0 1px 2px #00000014;
}

.sub-tab.active svg {
  color: var(--accent);
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-row {
  display: flex;
  gap: 16px;
}

.label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.field-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.field-hint strong {
  color: var(--text-secondary);
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
  width: 120px;
}

.urls-box {
  resize: vertical;
  min-height: 120px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.7;
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

.hint-text b {
  color: var(--text-secondary);
}

.error-line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 9px 12px;
  background: var(--red-bg);
  border-radius: 8px;
  font-size: 12px;
  color: var(--accent);
  line-height: 1.6;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--divider);
}

.foot-hint {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.foot-hint b {
  color: var(--text-secondary);
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
  opacity: 0.55;
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
  white-space: nowrap;
}

.ghost:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0;
}

/* ---------- 进行中 ---------- */

.phase {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.phase-icon {
  flex-shrink: 0;
  color: var(--blue);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.phase-text {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.phase-url {
  min-width: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-box {
  max-height: 300px;
  overflow-y: auto;
  padding: 10px 12px;
  background: #f7f8f9;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.log-empty {
  font-size: 12px;
  color: var(--text-tertiary);
}

.log-line {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.6;
}

.log-time {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--text-tertiary);
}

.log-msg {
  min-width: 0;
  color: var(--text-secondary);
  word-break: break-word;
}

.log-msg.warn {
  color: #b26a00;
}

.log-msg.error {
  color: var(--accent);
}

/* ---------- 报告页的候选列表 ---------- */

.block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.block-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: normal;
}

.candidates {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.candidate {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 9px 11px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  text-align: left;
  line-height: normal;
}

.candidate:hover {
  border-color: var(--text-tertiary);
}

.candidate.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.candidate.rejected {
  border-left: 2px solid var(--accent);
}

.candidate-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.candidate-flag {
  padding: 1px 6px;
  background: var(--accent);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  color: #ffffff;
}

.candidate-flag.bad {
  background: var(--red-bg);
  color: var(--accent);
}

.candidate-reason {
  font-size: 11px;
  color: var(--accent);
  line-height: 1.6;
}

.candidate-meta {
  font-size: 12px;
  color: var(--text-tertiary);
}

.candidate-meta code {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--text-secondary);
}

.candidate-sample {
  font-size: 12px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 批量队列 ---------- */

/*
 * 状态胶囊。基类必须在这里定义：scoped 样式不会跨组件继承，之前只写了
 * .run / .fail 两条 tone，基类缺失导致「排队中 / 完成 / 已取消」裸奔
 * （没有内边距与圆角），「运行中 / 未通过」只剩高亮底色、方角贴字。
 * 配色与尺寸对齐同页的 RunsSection / SourceListSection 的 .tag。
 */
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

/* 运行中 —— 蓝（同「事实」栏） */
.tag.run {
  background: var(--blue-bg);
  color: var(--blue);
}

/* 完成 —— 绿（同「信息」栏） */
.tag.ok {
  background: var(--green-bg);
  color: var(--green);
}

/* 未通过 —— 红（与全局 --accent 错误色一致） */
.tag.fail {
  background: var(--red-bg);
  color: var(--accent);
}

/* 排队中 —— 中性灰，但比默认略深一档，和「已取消」区分开 */
.tag.wait {
  background: #f1f2f4;
  color: var(--text-secondary);
}

/* 已取消 —— 最淡的中性灰（沿用基类色，显式写出便于对照） */
.tag.off {
  background: #f1f2f4;
  color: var(--text-tertiary);
}

.queue {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.queue-row {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.queue-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.queue-index {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.queue-info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.queue-url {
  min-width: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-sub {
  font-size: 12px;
  color: var(--text-secondary);
}

.queue-error {
  font-size: 12px;
  color: var(--accent);
  line-height: 1.6;
}

.queue-lastlog {
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.queue-detail {
  border-top: 1px solid var(--divider);
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
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

.mini-btn.on {
  border-color: var(--accent);
  color: var(--accent);
}

@media (max-width: 640px) {
  .form-row {
    flex-direction: column;
  }

  .queue-main {
    flex-wrap: wrap;
  }
}
</style>
