<script setup lang="ts">
/**
 * 导入接入（Phase 16 自 SourceModal 平移 + 批量新增）：
 *   单条导入 —— 输入 → 进行中（SSE）→ 启用条（启用该来源置顶）+ 验收报告 → （补救）候选替换重跑；
 *   批量导入 —— 粘贴 N 个地址排队逐个跑，聚合 SSE 推队列进度，行展开看报告。
 *
 * 状态全部在 `store/sourceImports.ts`（模块级）：切 tab / 组件重挂载都不丢；
 * 路由离开再回来由 `syncImportsOnMount` 重订阅对表。
 */
import { computed, reactive, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import SourceReport from '../SourceReport.vue'
import type { ImportJobStatus, SourceBatchChild } from '../../api/types'
import { api } from '../../api/client'
import { loadSources, notify, notifySuccess } from '../../store'
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
  handledJobs,
  LOG_LEVEL_TONE,
  refreshRecentRuns,
  registeredId,
  registeredLabel,
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

// ---------- 候选区收起 ----------
// 命名区域已全部自动接入时，候选区只是纠错入口——验收通过且接入了多个栏目就默认收起，
// 不杵在日常流程里（用户会把「选中一个重跑」误读成「接一个栏目就要重抓一遍」）。
const usedRegionCount = computed(
  () => (wizard.job?.candidates ?? []).filter((c) => c.selected && c.kind === 'region').length,
)
const showCandidates = ref(true)
watch(
  () => [wizard.job?.job_id, wizard.job?.report?.status, (wizard.job?.candidates ?? []).length] as const,
  () => {
    if (reportPassed.value && usedRegionCount.value >= 2) showCandidates.value = false
  },
  { immediate: true },
)

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

/** 批量展开行「启用该来源」：成功后行内换成「已启用」提示（作业是终态，状态不写回作业本身）。 */
async function enableExpanded() {
  if (expandedRegistered.value == null || !batch.expandedJob) return
  const label =
    typeof batch.expandedJob?.registered?.label === 'string'
      ? batch.expandedJob.registered.label
      : '该来源'
  const ok = await enableSourceById(expandedRegistered.value, label)
  if (ok && batch.expandedJob) handledJobs[batch.expandedJob.job_id] = 'enabled'
}

// ---------- 启用无头抓取模式（2026-10-21 用户裁定） ----------
// 静态管线拿不到内容（找不到列表/正文、JS 渲染空壳、配置不可执行）的失败条目，
// 右侧给一个人工确认按钮；点了才真保存（服务端轻量校验：无头浏览器渲染后有可见内容
// 即通过 → 已验收 · 已启用）。404 / robots / 5xx 这类「站点本身拒绝或不可达」的失败
// 服务端不下发 headless_eligible，不会出现按钮。

const savingHeadlessUrl = ref<string | null>(null)
/** 行内已确认启用的作业（批量队列行用：快照不再推送，本地记一份把按钮换成结果） */
const headlessSavedJobs = reactive<Record<string, true>>({})

/** 单条向导报告页：失败且 eligible、还没启用过 → 显示按钮。 */
const wizardHeadlessEligible = computed(
  () => !!wizard.job?.headless_eligible && !wizard.job?.browser_source,
)
/** 批量展开行：同上。 */
const expandedHeadlessEligible = computed(
  () => !!batch.expandedJob?.headless_eligible && !batch.expandedJob?.browser_source,
)

/** 启用无头抓取模式：调服务端轻量校验并落成 browser_driven 来源（已验收 · 已启用）。 */
async function enableHeadless(url: string, jobId?: string | null): Promise<boolean> {
  if (!url || savingHeadlessUrl.value) return false
  savingHeadlessUrl.value = url
  try {
    const result = await api.saveBrowserSource({ url, job_id: jobId ?? null })
    notifySuccess(
      `已启用无头抓取模式：来源「${result.label}」已验收 · 已启用——到抓取页选中它、填浏览目标即可抓取`,
    )
    await loadSources()
    void refreshRecentRuns()
    if (jobId) {
      headlessSavedJobs[jobId] = true
      // 展开行对象在内存里同步一份：按钮立即换成结果，收起再展开由服务端快照兜底
      if (batch.expandedJob?.job_id === jobId) {
        batch.expandedJob.browser_source = {
          source_id: result.source_id,
          key: result.key,
          label: result.label,
          created: result.created,
        }
      }
    }
    return true
  } catch (error) {
    notify(`启用无头抓取模式失败：${error instanceof Error ? error.message : String(error)}`)
    return false
  } finally {
    savingHeadlessUrl.value = null
  }
}

/** 单条向导报告页上的启用：成功后把向导重置回表单页，方便接着导入下一条。 */
async function enableHeadlessFromWizard(url: string, jobId: string | null) {
  const ok = await enableHeadless(url, jobId)
  if (ok) resetWizard()
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
          <!-- 主行动作置顶（2026-10-21 用户反馈）：验收报告很长，「启用该来源」原来在页尾，
               得一路翻到底才点得到。现在把启用条放到报告上面，状态说明 + 按钮一眼可见。 -->
          <div
            v-if="wizard.job?.browser_source || registeredId != null || wizardHeadlessEligible"
            class="enable-bar"
          >
            <p v-if="wizard.job?.browser_source" class="enable-note ok">
              <AppIcon name="circle-check" :size="13" />
              <span>
                已启用无头抓取模式：来源「{{ wizard.job.browser_source.label || '未命名来源' }}」
                已验收 · 已启用——可在抓取页直接选用
              </span>
            </p>
            <p v-else-if="registeredStatus === 'active'" class="enable-note ok">
              <AppIcon name="circle-check" :size="13" />
              <span>「{{ registeredLabel }}」已启用——可在抓取页直接选用</span>
            </p>
            <p v-else-if="wizardHeadlessEligible" class="enable-note">
              <AppIcon name="info" :size="13" />
              <span>静态管线没取到内容：确认后改用无头抓取模式，通过校验即已验收 · 已启用。</span>
            </p>
            <p v-else-if="reportPassed" class="enable-note">
              <AppIcon name="info" :size="13" />
              <span>
                已登记为 <b>{{ STATUS_LABEL[registeredStatus ?? 'draft'] }}</b>：通过验收后需人工点
                「启用该来源」才会进入抓取来源。
              </span>
            </p>
            <p v-else class="enable-note warn">
              <AppIcon name="triangle-alert" :size="13" />
              <span>这份配置没有通过验收，先换候选重跑或回到来源列表用「强制启用」。</span>
            </p>

            <div class="enable-actions">
              <!-- 静态管线拿不到内容的失败（服务端 headless_eligible）：人工确认后启用无头抓取 -->
              <button
                v-if="wizardHeadlessEligible"
                class="primary"
                type="button"
                :disabled="savingHeadlessUrl === (wizard.job?.url ?? '')"
                title="真用无头浏览器打开入口页轻量校验，通过后即成为已验收、已启用的来源；浏览目标在抓取配置里填"
                @click="enableHeadlessFromWizard(wizard.job?.url ?? '', wizard.job?.job_id ?? null)"
              >
                {{ savingHeadlessUrl === (wizard.job?.url ?? '') ? '校验中…' : '启用无头抓取模式' }}
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
          </div>

          <SourceReport
            :report="wizard.job?.report ?? null"
            :error-kind="wizard.job?.error_kind ?? null"
            :error="wizard.job?.error ?? null"
            :finished-at="wizard.job?.finished_at"
          />

          <!-- 候选清单：命名区域已全部自动接入（「当前使用」可以有多条），不需要再选；
               点其它候选 + 底部「重跑」只是「某个栏目选错了，用它替换」的补救动作。
               验收通过且已接入多个命名区域时默认收起——纠错能力留着，但不杵在日常流程里 -->
          <section v-if="wizard.job?.candidates.length" class="block">
            <button class="block-toggle" type="button" @click="showCandidates = !showCandidates">
              <AppIcon :name="showCandidates ? 'chevron-down' : 'chevron-right'" :size="13" />
              <span class="block-title">列表栏目候选（命名区域已自动全部接入，无需再选）</span>
              <span v-if="!showCandidates && usedRegionCount >= 2" class="candidate-flag">
                已接入 {{ usedRegionCount }} 个栏目
              </span>
            </button>
            <template v-if="showCandidates">
              <p class="block-sub">
                抓取时在抓取页按「发布机构」勾选要抓的栏目即可；点候选并「重跑」是纠错动作——
                命名区域只替换同位置的那一个，不会动其它已接入的栏目。
              </p>
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
                      <span v-if="candidate.kind === 'region'" class="candidate-flag">命名区域</span>
                      <span v-if="candidate.selected" class="candidate-flag">当前使用</span>
                      <span v-else-if="candidate.rejected_reason" class="candidate-flag bad">已试过 · 不行</span>
                      <span v-else-if="wizard.selectedCandidateIndex === candidate.index" class="candidate-flag">
                        已选中
                      </span>
                    </span>
                    <span class="candidate-meta">
                      {{ candidate.item_count ?? '?' }} 条 · <code>{{ candidate.list_selector }}</code>
                      <template v-if="candidate.scope_selector">
                        · 区域 <code>{{ candidate.scope_selector }}</code>
                        <template v-if="candidate.scope_index != null">#{{ candidate.scope_index + 1 }}</template>
                      </template>
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
            </template>
          </section>

          <p v-if="wizard.streamError" class="error-line">
            <AppIcon name="circle-x" :size="13" />
            <span>{{ wizard.streamError }}</span>
          </p>

          <div class="foot">
            <p v-if="wizard.job?.status === 'cancelled'" class="foot-hint">
              作业已取消，未登记任何来源
            </p>
            <p v-else-if="registeredId == null" class="foot-hint">
              这次没有登记新配置（失败不覆盖已在跑的来源）
            </p>
            <p v-else class="foot-hint">验收报告见上方，可逐条复核后再决定是否重新接入</p>
            <button class="ghost" type="button" @click="emit('switchTab', 'list')">回到列表</button>
            <button class="ghost" type="button" @click="resetWizard">重新接入</button>
            <button
              v-if="canRerunCandidate"
              class="ghost"
              type="button"
              :disabled="wizard.starting"
              title="补救动作：用选中的候选替换当前入选的栏目，再跑一次真实联网试运行（命名区域已全部接入，通常不需要点）"
              @click="rerunSelectedCandidate"
            >
              {{ wizard.starting ? '提交中…' : '替换为该候选并重跑' }}
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
                  <!-- 已启用无头抓取模式的结果（本地确认过或服务端快照带回） -->
                  <p v-if="child.browser_source || headlessSavedJobs[child.job_id]" class="queue-saved">
                    已启用无头抓取模式（已验收 · 已启用）——可在抓取页选用
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
                  <!-- 静态管线拿不到内容的失败行：右侧人工确认启用无头抓取（404/robots/5xx 无此按钮） -->
                  <button
                    v-if="
                      child.status === 'error' &&
                      child.headless_eligible &&
                      !child.browser_source &&
                      !headlessSavedJobs[child.job_id]
                    "
                    class="mini-btn headless"
                    type="button"
                    :disabled="savingHeadlessUrl === child.url"
                    title="真用无头浏览器打开入口页轻量校验，通过后即成为已验收、已启用的来源"
                    @click="enableHeadless(child.url, child.job_id)"
                  >
                    {{ savingHeadlessUrl === child.url ? '校验中…' : '启用无头抓取' }}
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
                    :finished-at="batch.expandedJob.finished_at"
                  />

                  <!-- 已启用过无头抓取模式：只显示一行结果；否则 eligible 的失败给右侧确认按钮 -->
                  <p v-if="batch.expandedJob.browser_source" class="auto-saved">
                    <AppIcon name="circle-check" :size="13" />
                    <span>
                      已启用无头抓取模式：来源「{{ batch.expandedJob.browser_source.label || '未命名来源' }}」
                      已验收 · 已启用——可在抓取页直接选用
                    </span>
                  </p>
                  <div v-if="expandedHeadlessEligible" class="foot">
                    <p class="foot-hint">
                      静态取数抓不到这个站的内容：启用无头抓取模式后，真用无头浏览器渲染入口页并轻量校验
                    </p>
                    <button
                      class="primary"
                      type="button"
                      :disabled="savingHeadlessUrl === (batch.expandedJob?.url ?? '')"
                      title="轻量校验通过即成为已验收、已启用的来源；浏览目标在抓取配置里填"
                      @click="enableHeadless(batch.expandedJob?.url ?? '', batch.expandedJob?.job_id ?? null)"
                    >
                      {{ savingHeadlessUrl === (batch.expandedJob?.url ?? '') ? '校验中…' : '启用无头抓取模式' }}
                    </button>
                  </div>

                  <div v-if="expandedRegistered != null" class="foot">
                    <p v-if="handledJobs[batch.expandedJob.job_id] === 'enabled'" class="foot-hint">
                      该来源已启用——可在抓取配置里选它作为来源
                    </p>
                    <template v-else>
                      <p class="foot-hint">验收通过的来源已登记为草案，启用后才进入抓取来源</p>
                      <button
                        v-if="expandedPassed"
                        class="primary"
                        type="button"
                        @click="enableExpanded"
                      >
                        启用该来源
                      </button>
                    </template>
                  </div>
                </template>
                <p v-else class="empty">
                  {{
                    child.gone
                      ? '进度快照已被服务端清理（进程内保留约 30 分钟）——完整报告见「接入记录」页'
                      : '读取作业详情…'
                  }}
                </p>
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

.block-sub {
  margin: -4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
  opacity: 0.85;
  line-height: 1.5;
}

.block-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
}

.block-toggle .block-title {
  flex: 1;
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

/* 兜底自动保存结果：一行绿提示，与错误行同款版式但用成功色 */
.auto-saved {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 9px 12px;
  background: var(--green-bg, #f3faf5);
  border-radius: 8px;
  font-size: 12px;
  color: var(--green, #1f7a3d);
  line-height: 1.6;
}

.auto-saved svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* ---------- 报告页顶部启用条 ---------- */
/*
 * 「启用该来源」从页尾提到报告上方（用户 2026-10-21 反馈：报告太长，要翻到底才点得到）。
 * 状态用一句话写在左侧、按钮固定在右侧，跨行时状态先占满一行、动作仍靠右。
 * 注意这里只是版式容器，按钮样式沿用 `.primary`，不要再单独定义一套主按钮。
 */
.enable-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 14px;
  padding: 10px 14px;
  background: #f7f8f9;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.enable-note {
  flex: 1 1 320px;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.enable-note svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--text-tertiary);
}

.enable-note b {
  color: var(--text-primary);
}

/* 已启用 / 已落成来源 —— 绿 */
.enable-note.ok,
.enable-note.ok svg {
  color: var(--green, #1f7a3d);
}

.enable-note.ok b {
  color: inherit;
}

/* 未通过验收 —— 红色警示，和「启用」按钮的禁用态区分开 */
.enable-note.warn svg {
  color: var(--accent);
}

.enable-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.queue-saved {
  font-size: 11px;
  color: var(--green, #1f7a3d);
  line-height: 1.6;
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

/* 「启用无头抓取」：失败行的主行动作，用主色描边与「详情」区分开 */
.mini-btn.headless {
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

.mini-btn.headless:hover:not(:disabled) {
  background: var(--accent-soft);
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
