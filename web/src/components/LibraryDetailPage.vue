<script setup lang="ts">
/**
 * 政策详情页（成果库进入）：元信息 + 事实/信息/知识三栏 + 政策正文存档。
 * 「重新提取」复用 /policies/{id}/reextract；「查看原文」打开 source_url。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import ExtractionColumns from './ExtractionColumns.vue'
import {
  libraryDetail,
  libraryDetailError,
  libraryDetailLoading,
  loadLibraryDetail,
  reextractLibraryPolicy,
  libraryReextracting,
} from '../store'

/** 由路由 `/library/:id(\\d+)` 注入（router.ts 的 props 函数把 params.id 转成 number）。 */
const props = defineProps<{ policyId: number }>()

const router = useRouter()

/**
 * 路由化后 id 来自 props 而非 store：进页 + 同页换 id（浏览器前进/后退、
 * 从详情页跳到另一篇）都要重拉；store 里的 detail 状态只管展示，不再持有 id。
 */
onMounted(() => void loadLibraryDetail(props.policyId))
watch(() => props.policyId, (id) => void loadLibraryDetail(id))

const expanded = ref(false)
const FULLTEXT_LIMIT = 300

const policy = computed(() => libraryDetail.value?.policy ?? null)
const extraction = computed(() => libraryDetail.value?.extraction ?? null)
const fulltext = computed(() => libraryDetail.value?.fulltext ?? '')
/** 从归档全文解析出的 gov.cn 元信息表（发文机关/来源/主题分类/公文种类/成文日期等） */
const fulltextMeta = computed(() => libraryDetail.value?.fulltext_meta ?? null)

/** 库字段优先，归档正文解析出的元信息表补缺（历史行 agency/doc_no/source_name 常为空） */
const headAgency = computed(() => policy.value?.agency || fulltextMeta.value?.agency || '')
const headDocNo = computed(() => policy.value?.doc_no || fulltextMeta.value?.doc_no || '')
const headSource = computed(() => policy.value?.source_name || fulltextMeta.value?.source || '')

/** 文号里可能出现的左括号符（半角/全角），文种就取它之前的那一段 */
const DOC_NO_BRACKETS = ['〔', '[', '（', '【']

/**
 * 文种不在详情契约里，由文号前缀推导——**必须与服务端 `_LIBRARY_LATEST_CTE` 的
 * 口径一致**（那是原始定义，这里是手抄镜像，改动要两边同步）：
 *   ① 取第一个括号符之前的部分（国发〔2026〕3号 → 国发，粤府〔2026〕1号 → 粤府），前缀限 1–6 字；
 *   ② 无括号的旧形态退回 gov.cn 名单。
 * 非 gov.cn 来源只有第 ① 条能推得出文种，这正是列表卡片以前对它显示为空的原因。
 */
const docType = computed(() => {
  const docNo = (policy.value?.doc_no ?? '').trim()
  if (!docNo) return ''
  let cut = -1
  for (const bracket of DOC_NO_BRACKETS) {
    const index = docNo.indexOf(bracket)
    if (index > 0 && (cut < 0 || index < cut)) cut = index
  }
  if (cut > 0 && cut <= 6) return docNo.slice(0, cut).trim()
  for (const prefix of ['国办发', '国办函', '国发', '国函', '国令']) {
    if (docNo.startsWith(prefix)) return prefix
  }
  return ''
})

const fulltextCount = computed(() => fulltext.value.length)
const fulltextShown = computed(() =>
  expanded.value ? fulltext.value : fulltext.value.slice(0, FULLTEXT_LIMIT),
)
const fulltextClamped = computed(
  () => fulltextCount.value > FULLTEXT_LIMIT && !expanded.value,
)

const extractInfo = computed(() => {
  const data = extraction.value
  if (!data) return ''
  const parts: string[] = []
  if (data.extracted_at) parts.push(`提取于 ${formatDateTime(data.extracted_at)}`)
  if (data.model) parts.push(`模型 ${data.model}`)
  parts.push(`事实 ${data.facts.length}`)
  parts.push(`要点 ${data.info.points.length}`)
  parts.push(`概念 ${data.knowledge.concepts.length}`)
  return parts.join(' · ')
})

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

function onOpenOrigin() {
  if (policy.value?.source_url) window.open(policy.value.source_url, '_blank', 'noopener,noreferrer')
}

function reload() {
  void loadLibraryDetail(props.policyId)
}

function backToLibrary() {
  void router.push({ name: 'library' })
}
</script>

<template>
  <div class="detail">
    <button class="back-row" type="button" @click="backToLibrary()">
      <AppIcon name="arrow-left" :size="14" />
      <span>返回成果库</span>
    </button>

    <!-- 加载骨架 -->
    <template v-if="libraryDetailLoading">
      <section class="head-card">
        <div class="skeleton skeleton-line tag-skel"></div>
        <div class="skeleton skeleton-title"></div>
        <div class="skeleton skeleton-line meta-skel"></div>
      </section>
      <section class="skeleton-columns">
        <div v-for="i in 3" :key="i" class="skeleton-column">
          <div class="skeleton skeleton-colhead"></div>
          <div class="skeleton-column-body">
            <span v-for="j in 3" :key="j" class="skeleton skeleton-line"></span>
          </div>
        </div>
      </section>
    </template>

    <!-- 加载失败 -->
    <section v-else-if="libraryDetailError" class="state-card">
      <AppIcon name="circle-x" :size="24" class="state-icon fail" />
      <p class="state-title">详情加载失败</p>
      <p class="state-desc">无法读取该篇政策，请检查后端服务后重试</p>
      <button class="state-btn" type="button" @click="reload">
        <AppIcon name="refresh-ccw" :size="12" />
        重新加载
      </button>
    </section>

    <template v-else-if="libraryDetail">
      <!-- 政策头部 -->
      <section class="head-card">
        <h1 class="head-title">{{ policy?.title }}</h1>

        <div class="tag-row">
          <span v-if="headAgency" class="head-tag">{{ headAgency }}</span>
          <span v-if="docType" class="head-tag">{{ docType }}</span>
          <span class="tag-spacer"></span>
          <span v-if="policy?.publish_date" class="head-date">
            <AppIcon name="calendar" :size="13" />
            发布于 {{ policy.publish_date }}
          </span>
        </div>

        <div class="meta-row">
          <span v-if="headDocNo" class="meta-item">
            <AppIcon name="file-text" :size="13" />
            文号：{{ headDocNo }}
          </span>
          <span v-if="headSource" class="meta-item">
            <AppIcon name="globe" :size="13" />
            来源：{{ headSource }}
          </span>
          <span v-if="fulltextMeta?.category" class="meta-item">
            <AppIcon name="folder" :size="13" />
            主题分类：{{ fulltextMeta.category }}
          </span>
          <span v-if="fulltextMeta?.doc_kind" class="meta-item">
            <AppIcon name="file-text" :size="13" />
            公文种类：{{ fulltextMeta.doc_kind }}
          </span>
          <span v-if="fulltextMeta?.signed_date" class="meta-item">
            <AppIcon name="calendar" :size="13" />
            成文日期：{{ fulltextMeta.signed_date }}
          </span>
        </div>

        <div class="action-row">
          <span class="extract-info">
            <AppIcon name="sparkles" :size="13" />
            {{ extraction?.status === 'extracting' ? '正在重新提取…' : extractInfo || '暂无提取信息' }}
          </span>
          <div class="action-btns">
            <button
              class="btn-outline"
              type="button"
              :disabled="libraryReextracting || extraction?.status === 'extracting'"
              @click="reextractLibraryPolicy(props.policyId)"
            >
              <AppIcon name="refresh-ccw" :size="13" />
              {{ libraryReextracting ? '提交中…' : '重新提取' }}
            </button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!policy?.source_url"
              @click="onOpenOrigin"
            >
              <AppIcon name="external-link" :size="13" />
              查看原文
            </button>
          </div>
        </div>
      </section>

      <!-- 提取中 -->
      <section v-if="extraction?.status === 'extracting'" class="state-card light">
        <AppIcon name="loader-circle" :size="24" class="state-icon spin" />
        <p class="state-title">正在提取…</p>
        <p class="state-desc">大模型正在解析该篇政策，完成后本页会自动刷新</p>
      </section>

      <!-- 三栏 -->
      <ExtractionColumns v-else :extraction="extraction!" />

      <!-- 政策正文 -->
      <section class="fulltext-card">
        <div class="fulltext-head">
          <span class="fulltext-title">
            <AppIcon name="file-text" :size="15" />
            政策正文（已归档全文）
          </span>
          <span class="tag-spacer"></span>
          <span v-if="fulltext" class="fulltext-count">共 {{ fulltextCount.toLocaleString() }} 字</span>
        </div>
        <template v-if="fulltext">
          <p class="fulltext-body">{{ fulltextShown }}{{ fulltextClamped ? '……' : '' }}</p>
          <button
            v-if="fulltextCount > FULLTEXT_LIMIT"
            class="expand-row"
            type="button"
            @click="expanded = !expanded"
          >
            {{ expanded ? '收起全文' : '展开全文' }}
            <AppIcon name="chevron-down" :size="13" :class="{ flip: expanded }" />
          </button>
        </template>
        <p v-else class="fulltext-empty">
          该篇归档于正文存档功能上线之前，暂无全文存档；可点「重新提取」补齐，或直接「查看原文」。
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---------- 返回行 ---------- */

.back-row {
  display: flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 2px 0;
  background: none;
  border: none;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
}

.back-row:hover {
  color: var(--text-primary);
}

/* ---------- 政策头部 ---------- */

.head-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 24px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.head-tag {
  padding: 4px 12px;
  background: #f1f2f4;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
  /* 多部门联合发文的机关串很长：允许收缩 + 省略号截断（窄屏下不撑破 head-card） */
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tag-spacer {
  flex: 1 1 0;
}

.head-date {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  flex-shrink: 0;
}

.head-title {
  font-size: 22px;
  font-weight: 700;
  line-height: 30px;
  color: var(--text-primary);
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.extract-info {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-outline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  line-height: normal;
  white-space: nowrap;
}

.btn-outline:hover:not(:disabled) {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}

.btn-outline:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-primary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #ffffff;
  line-height: normal;
  white-space: nowrap;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ---------- 状态块（错误 / 提取中） ---------- */

.state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 56px 24px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.state-card.light {
  padding: 32px 24px;
}

.state-icon {
  color: var(--text-tertiary);
  margin-bottom: 4px;
}

.state-icon.fail {
  color: var(--accent);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.state-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.state-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  text-align: center;
  max-width: 420px;
  line-height: 1.6;
}

.state-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 8px 14px;
  background: var(--card);
  border: 1px solid var(--accent);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
}

/* ---------- 政策正文 ---------- */

.fulltext-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.fulltext-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.fulltext-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.fulltext-count {
  font-size: 12px;
  color: var(--text-tertiary);
}

.fulltext-body {
  font-size: 13px;
  line-height: 22px;
  color: var(--text-secondary);
  white-space: pre-wrap;
}

.expand-row {
  display: flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  padding: 2px 0;
  background: none;
  border: none;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  line-height: normal;
}

.expand-row svg {
  transition: transform 0.15s ease;
}

.expand-row svg.flip {
  transform: rotate(180deg);
}

.fulltext-empty {
  font-size: 13px;
  line-height: 22px;
  color: var(--text-tertiary);
}

/* ---------- 骨架屏 ---------- */

.skeleton {
  display: inline-block;
  border-radius: 4px;
  background: linear-gradient(90deg, #e9ebee 25%, #f3f5f7 37%, #e9ebee 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.tag-skel {
  width: 140px;
  height: 22px;
  border-radius: 999px;
}

.skeleton-title {
  width: 60%;
  height: 24px;
  border-radius: 6px;
}

.meta-skel {
  width: 320px;
  height: 14px;
}

.skeleton-columns {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.skeleton-column {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.skeleton-colhead {
  width: 110px;
  height: 26px;
  border-radius: 999px;
}

.skeleton-column-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 10px;
  background: var(--bg);
}

.skeleton-line {
  width: 100%;
  height: 12px;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}
</style>
